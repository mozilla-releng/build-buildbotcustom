"""
pulse status plugin for buildbot

see http://hg.mozilla.org/users/clegnitto_mozilla.com/mozillapulse/ for pulse
code
"""
from datetime import tzinfo, timedelta, datetime
import re
import os.path
import time
import traceback

from twisted.internet.threads import deferToThread
from twisted.internet.defer import DeferredLock
from twisted.internet.task import LoopingCall
from twisted.internet import reactor
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
    """Transform an epoch time to a string representation of the form
    YYYY-mm-ddTHH:MM:SS+0000"""
    if t is None:
        return None
    elif isinstance(t, basestring):
        return t

    dt = datetime.fromtimestamp(t, UTC())
    return dt.strftime('%Y-%m-%dT%H:%M:%S%z')

def transform_times(event):
    """Replace epoch times in event with string representations of the time"""
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
    return name.replace(".", "_").replace(" ", "_")

def hexid(obj):
    return '<%s>' % hex(id(obj))

class PulseStatus(StatusPush):
    """
    Status pusher for Mozilla Pulse (an AMQP broker).

    Sends all events regarding activity on this master to the given publisher,
    which should be a mozillapulse.publishers.GenericPublisher instance.

    `ignoreBuilders`, if set, should be a list of strings or compiled regular
    expressions of builders that should *NOT* be watched.

    `send_logs`, if set, will enable sending log chunks to the message broker.

    `max_connect_time` (default 600) is the maximum length of time we'll hold a
    connection to the broker open.

    `max_idle_time` (default 300) is the maximum length of idle time we'll hold
    a connection to the broker open.

    `heartbeat_time` (default 900) is how often we generate heartbeat events.
    """

    compare_attrs = StatusPush.compare_attrs + ['publisher', 'ignoreBuilders',
            'send_logs']

    def __init__(self, publisher, ignoreBuilders=None, send_logs=False,
            max_connect_time=600, max_idle_time=300, heartbeat_time=900):
        self.publisher = publisher
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

        self.max_connect_time = max_connect_time
        self.max_idle_time = max_idle_time
        self.heartbeat_time = heartbeat_time

        self._disconnect_timer = None
        self._idle_timer = None

        # Lock to make sure only one thread is active at a time
        self._thread_lock = DeferredLock()

        # Set up heartbeat
        self._heartbeat_loop = LoopingCall(self.heartbeat)

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
        return StatusPush.stopService(self)

    def _disconnect(self, message):
        """Disconnect ourselves from the broker.

        We need to do this in a thread since disconnecting can require network
        access, which can block.
        """
        d = self._thread_lock.acquire()

        d.addCallback(lambda ign: log.msg(message))

        def clear_timers(ign):
            # Cancel and get rid of timers
            if self._disconnect_timer and self._disconnect_timer.active():
                self._disconnect_timer.cancel()
            self._disconnect_timer = None

            if self._idle_timer and self._idle_timer.active():
                self._idle_timer.cancel()
            self._idle_timer = None
        d.addCallback(clear_timers)

        d.addCallback(lambda ign: deferToThread(self.publisher.disconnect))

        def done(ign):
            self._thread_lock.release()
            return ign
        d.addBoth(done)
        return d

    def pushEvents(self):
        """Push some events to pulse"""
        d = self._thread_lock.acquire()

        # Set the idle/disconnect timers
        def set_timers(ign):
            # Set timer to disconnect
            if not self._disconnect_timer:
                self._disconnect_timer = reactor.callLater(
                        self.max_connect_time, self._disconnect,
                        "Pulse %s: Disconnecting after %s seconds" %
                            (hexid(self), self.max_connect_time))

            if self.max_idle_time:
                # Set timer for idle time
                if self._idle_timer:
                    self._idle_timer.cancel()
                self._idle_timer = reactor.callLater(
                        self.max_idle_time, self._disconnect,
                        "Pulse %s: Disconnecting after %s seconds idle" %
                            (hexid(self), self.max_idle_time))
        d.addCallback(set_timers)

        # Get the events
        def get_events(ign):
            events = self.queue.popChunk()
            if events:
                log.msg("Pulse %s: Initiating push of %i events" %
                        (hexid(self), len(events)))
            return events
        d.addCallback(get_events)

        # pushEvents is called synchronously from the base class, so we need to
        # do the publisher.publish calls in a thread, since they block
        def threadedPush(events):
            # Nothing to do!
            if not events:
                return []

            # Remove all but the last heartbeat
            last_heartbeat = None
            removed = 0
            for e in events[:]:
                if e['event'] == 'heartbeat':
                    if last_heartbeat is None:
                        last_heartbeat = e
                        continue
                    removed += 1
                    if e['id'] > last_heartbeat['id']:
                        events.remove(last_heartbeat)
                        last_heartbeat = e
                    else:
                        events.remove(e)
            if removed:
                log.msg("Pulse %s: Pruned %i old heartbeats" %
                        (hexid(self), removed))

            start = time.time()
            count = 0
            sent = 0
            heartbeats = 0
            while events:
                event = events.pop(0)
                count += 1
                if event['event'] == 'heartbeat':
                    heartbeats += 1

                try:
                    event['master_name'] = self.status.botmaster.master_name
                    event['master_incarnation'] = \
                            self.status.botmaster.master_incarnation
                    # Transform time tuples to standard pulse time format
                    msg = BuildMessage(transform_times(event))
                    self.publisher.publish(msg)
                    sent += 1
                except:
                    # put at the end, in case it's a problematic message
                    # somehow preventing us from moving on
                    events.append(event)
                    log.msg("Pulse %s: Error handling message %s: %s" %
                            (hexid(self), event['event'],
                                traceback.format_exc()))
                    self.publisher.disconnect()
                    break
            end = time.time()
            log.msg("Pulse %s: Processed %i, sent %i events (%i heartbeats) "
                        "in %.2f seconds" %
                        (hexid(self), count, sent, heartbeats, (end-start)))
            # Anything left in here should be retried
            return events
        d.addCallback(lambda events: deferToThread(threadedPush, events))

        def donePush(retry):
            if retry:
                log.msg("Pulse %s: retrying %s events" %
                        (hexid(self), len(retry)))
                self.queue.insertBackChunk(retry)
        d.addCallback(donePush)

        def _finished(ign):
            self._thread_lock.release()
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

