from datetime import tzinfo, timedelta, datetime
import re
import os.path
import time

from twisted.internet.threads import deferToThread
from twisted.python import log

from mozillapulse.messages.build import BuildMessage

from buildbot.status.status_push import StatusPush

ZERO = timedelta(0)
HOUR = timedelta(hours=1)

# A UTC class.

class UTC(tzinfo):
    """UTC"""

    def utcoffset(self, dt):
        return ZERO

    def tzname(self, dt):
        return "UTC"

    def dst(self, dt):
        return ZERO

def transform_time(t):
    if t is None:
        return None
    dt = datetime.fromtimestamp(t, UTC())
    return dt.strftime('%Y-%m-%dT%H:%M:%S%z')

def transform_times(event):
    if isinstance(event, dict):
        retval = {}
        for key, value in event.items():
            if key == 'times' and len(value) == 2:
                retval[key] = [transform_time(t) for t in value]
            else:
                retval[key] = transform_times(value)
    else:
        retval = event
    return retval

def escape(name):
    return name.replace(".", "_")

class PulseStatus(StatusPush):
    compare_attrs = StatusPush.compare_attrs + ['publisher', 'ignoreBuilders',
            'send_logs']

    def __init__(self, publisher, ignoreBuilders=None, send_logs=False):
        self.publisher = publisher
        self.send_logs = send_logs
        self.readyForMore = True
        self.ignoreBuilders = []
        if ignoreBuilders:
            for i in ignoreBuilders:
                if isinstance(i, basestring):
                    self.ignoreBuilders.append(re.compile(i))
                else:
                    assert hasattr(i, 'match') and callable(i.match)
                    self.ignoreBuilders.append(i)
        self.watched = []
        StatusPush.__init__(self, PulseStatus.pushEvents, filter=False)

    def disownServiceParent(self):
        self.status.unsubscribe(self)
        for w in self.watched:
            w.unsubscribe(self)
        return StatusPush.disownServiceParent(self)

    def pushEvents(self):
        # Try again later
        if not self.readyForMore:
            return

        # This function is called synchronously from the base class
        # So we need to do the publisher.publish calls asynchronously
        events = self.queue.popChunk()
        def threadedPush():
            start = time.time()
            count = 0
            for event in events:
                count += 1
                try:
                    event['master_name'] = self.status.botmaster.master_name
                    event['master_incarnation'] = self.status.botmaster.master_incarnation
                    # Transform time tuples to standard pulse time format
                    event = transform_times(event)
                    msg = BuildMessage(event)
                    self.publisher.publish(msg)
                except:
                    # TODO: Re-queue it?
                    log.msg("Error handling message %s" % event)
                    log.err()
                    self.publisher.disconnect()
                    continue
            end = time.time()
            log.msg("Pulse %s: Sent %i events in %.2f seconds" % (self, count, (end-start)))
        log.msg("Pulse %s: Initiating push of %i events" % (self, len(events)))
        self.readyForMore = False
        d = deferToThread(threadedPush)
        def _finished(ign):
            self.readyForMore = True
            return ign
        d.addBoth(_finished)
        d.addErrback(log.err)
        return d

    def builderAdded(self, builderName, builder):
        if self.stopped:
            return None

        for i in self.ignoreBuilders:
            try:
                if i.match(builderName):
                    return None
            except:
                log.msg("Exception matching builderName with %s" % str(i))
                log.msg("Shutting down PulseStatus - not sure if we're ignoring the right stuff!")
                log.err()
                self.stopService()
                if self.parent:
                    self.disownServiceParent()
        self.watched.append(builder)
        return self

    def _translateBuilderName(self, builderName):
        builder = self.status.getBuilder(builderName)
        return os.path.basename(builder.basedir)

    def buildStarted(self, builderName, build):
        builderName = escape(self._translateBuilderName(builderName))
        self.push("build.%s.%i.started" % (builderName, build.number), build=build)
        return self

    def buildFinished(self, builderName, build, results):
        builderName = escape(self._translateBuilderName(builderName))
        self.push("build.%s.%i.finished" % (builderName, build.number), build=build, results=results)

    def slaveConnected(self, slavename):
        self.push("slave.%s.connected" % escape(slavename), slave=self.status.getSlave(slavename))

    def slaveDisconnected(self, slavename):
        self.push("slave.%s.disconnected" % escape(slavename), slavename=slavename)

    def changeAdded(self, change):
        self.push("change.%i.added" % change.number, change=change)

    def requestSubmitted(self, request):
        builderName = escape(self._translateBuilderName(request.getBuilderName()))
        self.push("request.%s.submitted" % builderName, request=request)

    def requestCancelled(self, builder, request):
        builderName = escape(self._translateBuilderName(builder.name))
        self.push("request.%s.cancelled" % builderName, request=request)

    def stepStarted(self, build, step):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.started" % (builderName, build.number, escape(step.name)),
                properties=build.getProperties().asList(),
                step=step)
        if self.send_logs:
            return self

    def stepFinished(self, build, step, results):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.finished" % (builderName, build.number, escape(step.name)),
                properties=build.getProperties().asList(),
                step=step,
                results=results)

    def logStarted(self, build, step, log):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.log.%s.started" % (builderName, build.number, escape(step.name), log.name))
        return self

    def logChunk(self, build, step, log, channel, text):
        # TODO: Strip out bad UTF-8 characters
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.log.%s.chunk" % (builderName, build.number, escape(step.name), log.name),
                channel=channel,
                text=text,
                )

    def logFinished(self, build, step, log):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.log.%s.finished" % (builderName, build.number, escape(step.name), log.name))
        return self

    # Stuff we ignore
    def buildsetSubmitted(self, buildset):
        pass

    def builderChangedState(self, builderName, state):
        pass

    def builderRemoved(self, builderName):
        pass

    def stepETAUpdate(self, build, step, ETA, expectations):
        pass

    def stepTextChanged(self, build, step, text):
        pass

    def stepText2Changed(self, build, step, text2):
        pass

