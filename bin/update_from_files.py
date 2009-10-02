import buildbotcustom.status.db.model as model
import cPickle, os, re, time, sys
from datetime import datetime

def getBuildNumbers(builder, last_time):
    files = os.listdir(builder)
    def _sortfunc(x):
        try:
            return int(x)
        except:
            return x
    files.sort(key=_sortfunc)

    retval = []
    for f in files:
        if re.match("^\d+$", f):
            p = os.path.join(builder, f)
            if os.path.getmtime(p) < last_time:
                continue
            retval.append(f)
    return retval

def getBuild(builder, number):
    try:
        return cPickle.load(open(os.path.join(builder, number)))
    except:
        return None

def getBuilder(builder):
    # Monkey patch BuilderStatus so that it doesn't clear out list of slaves
    # when unpickling
    from buildbot.status.builder import BuilderStatus
    if '__setstate__' in BuilderStatus.__dict__:
        del BuilderStatus.__dict__['__setstate__']
    return cPickle.load(open(os.path.join(builder, 'builder')))

def updateBuilderSlaves(session, builder, db_builder):
    bb_slaves = set(s for s in builder.slavenames)
    db_slaves = set()
    db_slaves_by_name = {}
    for builder_slave in db_builder.slaves:
        db_slaves.add(builder_slave.slave.name)
        db_slaves_by_name[builder_slave.slave.name] = builder_slave

    # Which slaves were added to this builder
    new_slaves = bb_slaves - db_slaves
    # Which slaves were removed from this builder
    old_slaves = db_slaves - bb_slaves

    for s in new_slaves:
        bs = model.BuilderSlave(added=datetime.now(), slave=model.Slave.get(session, s))
        db_builder.slaves.append(bs)
        session.add(bs)

    for s in db_builder.slaves[:]:
        if s.slave.name in old_slaves:
            # Mark it as removed
            db_slaves_by_name[s.slave.name].removed = datetime.now()

    session.commit()

def updateSlaveTimes(session, master, builder, db_builder, last_time):
    db_slaves = {}
    for builder_slave in db_builder.slaves:
        db_slaves[builder_slave.slave.name] = builder_slave

    # Fetch all the events from the database for these slaves
    events = session.query(model.MasterSlave).\
                filter(model.MasterSlave.slave_id.in_([slave.slave.id for slave in db_builder.slaves]))

    if last_time:
        events = events.filter(model.MasterSlave.connected > datetime.utcfromtimestamp(last_time))

    events = events.order_by(model.MasterSlave.connected.asc()).all()

    events_by_slave = {}
    for e in events:
        if e.slave.name not in events_by_slave:
            events_by_slave[e.slave.name] = []
        events_by_slave[e.slave.name].append(e)

    for e in builder.events:
        if len(e.text) == 2 and e.text[0] in ("connect", "disconnect"):
            name = e.text[1]
            if e.started < last_time:
                continue
            if name not in db_slaves:
                # We don't know about this slave
                continue
            if name not in events_by_slave:
                events_by_slave[name] = []
            slave_events = events_by_slave[name]
            t = datetime.utcfromtimestamp(int(e.started))
            if e.text[0] == "connect":
                # This slave just connected to this builder
                # Check if we've got an entry earlier than this that hasn't been disconnected yet
                found = False
                for event in reversed(slave_events):
                    if event.connected < t and not event.disconnected:
                        found = True
                        break
                    if event.connected == t:
                        found = True
                        break

                if not found:
                    # Didn't find an event, need to create one!
                    event = session.query(model.MasterSlave).filter_by(connected=t, slave=db_slaves[name].slave, master=master).first()
                    if event:
                        print t
                        for e in reversed(slave_events):
                            print e.connected, e.connected-t, e.connected == t
                        raise ValueError("Shouldn't be here!")
                    event = model.MasterSlave(connected=t, slave=db_slaves[name].slave, master=master)
                    session.add(event)

                    slave_events.append(event)
                    events.append(event)
                    slave_events.sort(key=lambda x:x.connected)
                    events.sort(key=lambda x:x.connected)
            else:
                # If this is a disconnect event, find the last connect event and mark it as disconnected
                found = False
                for event in reversed(slave_events):
                    if event.connected < t:
                        event.disconnected = t
                        found = True
                        break
                # If we didn't find anything, ignore it for now
        elif e.text == ['master', 'shutdown']:
            # Set any slaves that were connected at the time to disconnected
            t = datetime.utcfromtimestamp(e.started)
            for event in reversed(events):
                if event.connected < t and not event.disconnected:
                    event.disconnected = t

    session.commit()


def updateFromFiles(session, master_url, master_name, builders, last_time, update_times):
    master = model.Master.get(session, master_url)
    master.name = unicode(master_name)
    i = 0
    n = 0
    allBuilds = {}
    for builder in builders:
        buildNumbers = getBuildNumbers(builder, last_time)
        allBuilds[builder] = buildNumbers
        n += len(buildNumbers)

    s = time.time()

    for builder in builders:
        master = session.merge(master)
        builder_name = os.path.basename(builder)
        bb_builder = getBuilder(builder)
        db_builder = model.Builder.get(session, builder_name, master.id)
        db_builder.category = unicode(bb_builder.category)

        updateBuilderSlaves(session, bb_builder, db_builder)
        if update_times:
            updateSlaveTimes(session, master, bb_builder, db_builder, last_time)

        builds = allBuilds[builder]
        bn = len(builds)

        for j, buildNumber in enumerate(builds):
            master = session.merge(master)
            db_builder = session.merge(db_builder)
            complete = i / float(n)
            if complete == 0:
                eta = 0
            else:
                eta = (time.time() - s) / (complete)
                eta = (1-complete) * eta
            print builder, buildNumber, "%i/%i" % (j+1, bn), "%.2f%% complete" % (100* complete), "ETA in %i seconds" % eta
            i += 1
            build = getBuild(builder, buildNumber)
            if not build:
                continue
            starttime = None
            endtime = None
            if build.started:
                starttime = datetime.utcfromtimestamp(build.started)
            if build.finished:
                endtime = datetime.utcfromtimestamp(build.finished)

            q = session.query(model.Build).filter_by(
                    builder=db_builder,
                    buildnumber=build.number,
                    starttime=starttime,
                    endtime=endtime,
                    )
            db_build = q.first()
            if not db_build:
                db_build = model.Build.fromBBBuild(session, build, builder_name, master.id)
            else:
                db_build.updateFromBBBuild(session, build)
            session.commit()
            session.expunge_all()
    return i

if __name__ == "__main__":
    from optparse import OptionParser

    parser = OptionParser("%prog [options] builders")
    parser.add_option("-d", "--database", dest="database", help="database url")
    parser.add_option("-m", "--master", dest="master", help="master url (buildbotURL in the master.cfg file)")
    parser.add_option("-n", "--description", dest="name", help="human friendly name for master")
    parser.add_option("", "--times", dest="times", help="update slave connect/disconnect times", action="store_true", default=False)

    options, args = parser.parse_args()

    if not options.database:
        parser.error("Must specify a database to connect to")

    if not options.master:
        parser.error("Must specify a master url")

    if not args:
        parser.error("Must specify at least one builder or directory")

    builders = []
    for a in args:
        if os.path.exists(os.path.join(a, "builder")):
            builders.append(a)
        else:
            for d in os.listdir(a):
                p = os.path.join(a, d)
                if os.path.exists(os.path.join(a, d, "builder")):
                    builders.append(p)

    session = model.connect(options.database)()

    started = time.time()
    try:
        last_time = float(open("last_time.txt").read())
    except:
        last_time = 0

    updated = updateFromFiles(session, options.master, options.name, builders, last_time, options.times)

    print "Updated", updated, "builds"

    open("last_time.txt", "w").write(str(started))
