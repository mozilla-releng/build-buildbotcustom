"""
pulse status plugin for buildbot
"""
import re
import os.path
import time
import traceback

from twisted.internet.threads import deferToThread
from twisted.internet.defer import DeferredLock
from twisted.internet.task import LoopingCall
from twisted.internet import reactor
from twisted.python import log

from buildbot.status.status_push import StatusPush
from buildbot.util import json

def escape(name):
    return name.replace(".", "_").replace(" ", "_")

def hexid(obj):
    return '<%s>' % hex(id(obj))

class PulseStatus(StatusPush):
    """
    Status pusher for Mozilla Pulse (an AMQP broker).

    Sends all events regarding activity on this master to the given queue,
    which should be a buildbotcustom.status.queue.QueueDir instance.

    `ignoreBuilders`, if set, should be a list of strings or compiled regular
    expressions of builders that should *NOT* be watched.

    `send_logs`, if set, will enable sending log chunks to the message broker.

    `heartbeat_time` (default 900) is how often we generate heartbeat events.
    """

    compare_attrs = StatusPush.compare_attrs + ['queuedir', 'ignoreBuilders',
            'send_logs']

    def __init__(self, queuedir, ignoreBuilders=None, send_logs=False,
            heartbeat_time=900):
        self.queuedir = queuedir
        self.send_logs = send_logs

        self.ignoreBuilders = []
        if ignoreBuilders:
            for i in ignoreBuilders:
                if isinstance(i, basestring):
                    self.ignoreBuilders.append(re.compile(i))
                else:
                    assert hasattr(i, 'match') and callable(i.match)
                    self.ignoreBuilders.append(i)
        self.watched = []

        # Set up heartbeat
        self.heartbeat_time = heartbeat_time
        self._heartbeat_loop = LoopingCall(self.heartbeat)

        # Wait 10 seconds before sending our stuff
        self.push_delay = 10
        self.delayed_push = None

        StatusPush.__init__(self, PulseStatus.pushEvents, filter=False)

    def setServiceParent(self, parent):
        StatusPush.setServiceParent(self, parent)

        # Start heartbeat
        # This should be done in startService, but our base class isn't
        # behaving
        # See http://trac.buildbot.net/ticket/1793
        self._heartbeat_loop.start(self.heartbeat_time)

    # Stubbed out for later
    #def startService(self):
        #self._heartbeat_loop.start(self.heartbeat_time)
        #return StatusPush.startService(self)

    def stopService(self):
        log.msg("Pulse %s: shutting down" % (hexid(self),))
        if self._heartbeat_loop.running:
            self._heartbeat_loop.stop()

        self.status.unsubscribe(self)
        for w in self.watched:
            w.unsubscribe(self)

        # Write out any pending events
        if self.delayed_push:
            self.delayed_push.cancel()
            self.delayed_push = None

        while self.queue.nbItems() > 0:
            self._do_push()
        return StatusPush.stopService(self)

    def pushEvents(self):
        """Trigger a push"""
        if not self.delayed_push:
            self.delayed_push = reactor.callLater(self.push_delay, self._do_push)

    def _do_push(self):
        """Push some events to pulse"""
        self.delayed_push = None

        # Get the events
        events = self.queue.popChunk()

        # Nothing to do!
        if not events:
            return

        # List of json-encodable messages to write to queuedir
        to_write = []
        start = time.time()
        count = 0
        heartbeats = 0
        for e in events:
            count += 1
            if e['event'] == 'heartbeat':
                heartbeats += 1

            e['master_name'] = self.status.botmaster.master_name
            e['master_incarnation'] = \
                    self.status.botmaster.master_incarnation

            # Transform time tuples to standard pulse time format
            to_write.append(e)
        end = time.time()
        log.msg("Pulse %s: Processed %i events (%i heartbeats) "
                    "in %.2f seconds" %
                    (hexid(self), count, heartbeats, (end-start)))
        try:
            self.queuedir.add(json.dumps(to_write))
        except:
            # Try again later?
            self.queue.insertBackChunk(events)
            log.err()

        # If we still have more stuff, send it in a bit
        if self.queue.nbItems() > 0:
            self.pushEvents()

    def builderAdded(self, builderName, builder):
        if self.stopped:
            return None

        for i in self.ignoreBuilders:
            try:
                if i.match(builderName):
                    return None
            except:
                log.msg("Exception matching builderName with %s" % str(i))
                log.msg("Shutting down PulseStatus - "
                    "not sure if we're ignoring the right stuff!")
                log.err()
                self.stopService()
                if self.parent:
                    self.disownServiceParent()
        self.watched.append(builder)
        return self

    def _translateBuilderName(self, builderName):
        builder = self.status.getBuilder(builderName)
        return os.path.basename(builder.basedir)

    def heartbeat(self):
        """send a heartbeat event"""
        # We're called from inside a LoopingCall, so make sure we never leak an
        # exception up to it, otherwise we'll never be called again!!!
        # OH NOES!
        try:
            log.msg("Pulse %s: heartbeat" % (hexid(self),))
            self.push("heartbeat")
        except:
            log.msg("Pulse %s: failed to send heartbeat" % (hexid(self),))
            log.err()

    ### Events we publish

    def buildStarted(self, builderName, build):
        builderName = escape(self._translateBuilderName(builderName))
        self.push("build.%s.%i.started" % (builderName, build.number),
                build=build)
        return self

    def buildFinished(self, builderName, build, results):
        builderName = escape(self._translateBuilderName(builderName))
        self.push("build.%s.%i.finished" % (builderName, build.number),
                build=build, results=results)

    def slaveConnected(self, slavename):
        self.push("slave.%s.connected" % escape(slavename),
                slave=self.status.getSlave(slavename))

    def slaveDisconnected(self, slavename):
        self.push("slave.%s.disconnected" % escape(slavename),
                slavename=slavename)

    def changeAdded(self, change):
        self.push("change.%i.added" % change.number, change=change)

    def requestSubmitted(self, request):
        builderName = escape(self._translateBuilderName(
            request.getBuilderName()))
        self.push("request.%s.submitted" % builderName, request=request)

    def requestCancelled(self, builder, request):
        builderName = escape(self._translateBuilderName(builder.name))
        self.push("request.%s.cancelled" % builderName, request=request)

    def stepStarted(self, build, step):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.started" %
                (builderName, build.number, escape(step.name)),
                properties=build.getProperties().asList(),
                step=step)
        # If logging is enabled, return ourself to subscribe to log events for
        # this step
        if self.send_logs:
            return self

    def stepFinished(self, build, step, results):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.finished" %
                (builderName, build.number, escape(step.name)),
                properties=build.getProperties().asList(),
                step=step,
                results=results)

    ### Optional logging events

    def logStarted(self, build, step, log):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.log.%s.started" %
                (builderName, build.number, escape(step.name), log.name))
        return self

    def logChunk(self, build, step, log, channel, text):
        # TODO: Strip out bad UTF-8 characters
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.log.%s.chunk" %
                (builderName, build.number, escape(step.name), log.name),
                channel=channel,
                text=text,
                )

    def logFinished(self, build, step, log):
        builderName = escape(self._translateBuilderName(build.builder.name))
        self.push("build.%s.%i.step.%s.log.%s.finished" %
                (builderName, build.number, escape(step.name), log.name))
        return self

    ### Events we ignore

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

