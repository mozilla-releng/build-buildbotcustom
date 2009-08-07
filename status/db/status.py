from datetime import datetime

import sqlalchemy

from twisted.python import log

from buildbot.status import base
from buildbot.status.builder import FAILURE, HEADER
import buildbot.scripts.checkconfig as checkconfig

import model
reload(model)

class DBBuildStatus(base.StatusReceiver):
    """This class monitors the status for an individual build.  It receives
    stepStarted, stepFinished, logStarted, logFinished and logChunk
    notifications from buildbot and passes the notifications to subscribers,
    along with the database step object (the logChunk notification receives
    only the database id for the step, to prevent excessive database lookups).

    It updates the database on stepStarted and stepFinished events."""
    def __init__(self, build_id, subscribers=None):
        self.build_id = build_id

        self.subscribers = subscribers or []

        self.current_step = None
        self.current_step_id = None

        # Keep a local reference to the session maker On a buildbot reconfig,
        # model.Session will be reset to None, and we might get
        # stepStarted/stepFinished notifications while the reconfig is
        # happening.
        self.Session = model.Session

    def stepStarted(self, build, step):
        """Create this step in the database, and give it a start time"""
        session = self.Session()
        try:
            b = session.query(model.Build).options(model.eagerload('steps')).get(self.build_id)
            s = model.Step.get(session, name=step.name, build_id=self.build_id)
            s.starttime = datetime.utcfromtimestamp(step.started)
            s.description = step.text
            session.commit()
            # Keep track of our current step
            self.current_step = s
            self.current_step_id = s.id
            # Notify any of our subscribers of stepStarted
            for sub in self.subscribers:
                if hasattr(sub, 'stepStarted'):
                    try:
                        sub.stepStarted(build, step, s)
                    except:
                        log.msg("DBERROR: Couldn't notify subscriber %s of step starting" % sub)
                        log.err()
            return self
        except:
            log.msg("DBERROR: Couldn't add step: name=%s, build_id=%s" % (step.name, self.build_id))
            log.err()
        finally:
            session.close()

    def stepFinished(self, build, step, results):
        """Mark this step as finished in the database, giving it an endtime,
        saving the status, description, and updating the build properties."""
        session = self.Session()
        try:
            # We may not have been called with stepStarted, so the step may not
            # exist yet in the database, or we may not know about it if it
            # does.  This can happen if the master is reconfigured while steps
            # are currently active.
            if not self.current_step:
                s = model.Step.get(session, name=step.name, build_id=self.build_id)
                # This may not be set
                if s.starttime:
                    s.starttime = datetime.utcfromtimestamp(step.started)
                s.description = step.text
            else:
                s = session.merge(self.current_step)

            if step.finished:
                s.endtime = datetime.utcfromtimestamp(step.finished)
            s.status = results[0]
            s.description = step.text
            # Update the properties
            b = session.query(model.Build).get(self.build_id)
            if b:
                b.properties = model.Property.fromBBProperties(session, build.getProperties())

            session.commit()
            # Notify our subscribers that the step is done
            for sub in self.subscribers:
                if hasattr(sub, 'stepFinished'):
                    try:
                        sub.stepFinished(build, step, results, s)
                    except:
                        log.msg("DBERROR: Couldn't notify subscriber %s of step finishing" % sub)
                        log.err()
        except:
            log.msg("DBERROR: Couldn't mark step as finished: name=%s, build_id=%s" % (step.name, self.build_id))
            log.err()
        finally:
            self.current_step = None
            self.current_step_id = None
            session.close()

    def logStarted(self, build, step, l):
        # We don't track logs in the database, so if we have no subscribers
        # there's nothing to do here.  If we have subscribers, then let them
        # know about the new log, as well as giving them the database step
        # object.
        if self.subscribers:
            session = self.Session()
            retval = None
            try:
                s = session.merge(self.current_step)
                for sub in self.subscribers:
                    if hasattr(sub, 'logStarted'):
                        try:
                            sub.logStarted(build, step, l, s)
                            retval = self
                        except:
                            log.msg("DBERROR: Couldn't notify subscriber %s of log starting" % sub)
                            log.err()
            except:
                log.msg("DBERROR: logStarted")
                log.err()
            finally:
                session.close()
            return retval

    def logFinished(self, build, step, l):
        # We don't track logs in the database, so if we have no subscribers
        # there's nothing to do here.  If we have subscribers, then let them
        # know the log is done, as well as giving them the database step
        # object.
        if self.subscribers:
            session = self.Session()
            try:
                s = session.merge(self.current_step)
                for sub in self.subscribers:
                    if hasattr(sub, 'logFinished'):
                        try:
                            sub.logFinished(build, step, l, s)
                        except:
                            log.msg("DBERROR: Couldn't notify subscriber %s of log finishing" % sub)
                            log.err()
            except:
                log.msg("DBERROR: logFinished")
                log.err()
            finally:
                session.close()

    def logChunk(self, build, step, l, channel, text):
        # We don't track logs in the database, so if we have no subscribers
        # there's nothing to do here.  If we have subscribers, then let them
        # know the log has more output, as well as giving them the database
        # step id
        if self.subscribers:
            try:
                for sub in self.subscribers:
                    if hasattr(sub, 'logChunk'):
                        try:
                            sub.logChunk(build, step, l, channel, text, self.current_step_id)
                        except:
                            log.msg("DBERROR: Couldn't notify subscriber %s of log chunk" % sub)
                            log.err()
            except:
                log.msg("DBERROR: logChunk")
                log.err()

class DBStatus(base.StatusReceiverMultiService):
    """Database Status plugin for Buildbot.

    This plugin records all information about builders, builds, and steps, in an SQL database."""
    def __init__(self, dburl, name=None, subscribers=None):
        """
        dburl:          an SQLAlchemy database URL that specifies how to connect to the SQL database
        subscribers:    a list of objects to receive status notifications augmented with database information.
        name:           a human friendly name for this master
        """
        base.StatusReceiverMultiService.__init__(self)

        # Mapping of buildbot Request objects to database Request objects.
        # This is used to make sure the correct request objects get associated
        # with new builds when they start.
        self.request_mapping = {}
        self.subscribers = subscribers or []
        self.builders = []
        self.dburl = dburl
        self.name = name

    def setServiceParent(self, parent):
        log.msg("Starting DB Status handler")
        base.StatusReceiverMultiService.setServiceParent(self, parent)

        # Skip doing anything if we're just doing a checkconfig.  We don't want to
        # potentially change the state of the database on a checkconfig.
        if isinstance(parent, checkconfig.ConfigLoader):
            return

        # Keep a local reference to the session maker On a buildbot reconfig,
        # model.Session will be reset to None, and we might get
        # stepStarted/stepFinished notifications while the reconfig is
        # happening.
        self.Session = model.connect(self.dburl, echo_pool=True, pool_recycle=60)

        # Let our subscribers know about the database connection
        # This gives them the opportunity to set up their own tables, etc.
        for sub in self.subscribers:
            if hasattr(sub, 'databaseConnected'):
                try:
                    sub.databaseConnected(self.engine)
                except:
                    log.msg("DBERROR: Couldn't notify subscriber %s of database connection" % sub)
                    log.err()

        self.setup()

    def disownServiceParent(self):
        log.msg("Stopping DB Status handler")
        base.StatusReceiverMultiService.disownServiceParent(self)
        try:
            self.status.unsubscribe(self)
        except:
            log.msg("DBERROR: Couldn't unsubscribe from the master")
            log.err()

        for builder in self.builders:
            try:
                builder.unsubscribe(self)
            except:
                log.msg("DBERROR: Couldn't unsubscribe from builder %s" % builder.name)
                log.err()

    def setup(self):
        self.status = self.parent.getStatus()
        session = self.Session()
        try:
            # Find the master in the database.  If it doesn't exist yet, now's
            # the time to create it!
            master_url = self.status.getBuildbotURL()
            master = model.Master.get(session, master_url)
            master.name = self.name
            session.commit()
            self.master_id = master.id

            # Add all the master's slaves to the database
            for slave_name in self.status.getSlaveNames():
                s = model.Slave.get(session, slave_name)
                session.add(s)

            # Check any builds that aren't finished in the database
            # they could still be running (if the master was just reconfigured),
            # or they could now be stopped (if the master was stopped)
            for build in session.query(model.Build).filter_by(endtime=None, master=master):
                force_done = False
                master_build = None
                lost = False
                try:
                    master_build = self.status.getBuilder(build.builder.name).getBuildByNumber(build.buildnumber)
                    if master_build.finished:
                        force_done = True
                        end_time = datetime.utcfromtimestamp(master_build.finished)
                        result = master_build.results
                except (IOError, IndexError):
                    # The master doesn't have information about this build, so
                    # that means it's not active.  Mark it as finished as of now, and FAILED
                    force_done = True
                    end_time = datetime.now()
                    lost = True

                if force_done:
                    log.msg("DBMSG: Marking build %s %i as done" % (build.builder.name, build.buildnumber))
                    build.endtime = end_time
                    if lost:
                        build.lost = True
                    else:
                        build.result = result
                    # Try and synchronize the information we have in the
                    # database about the build steps with the information the
                    # master has.  If the master has no information about the
                    # steps any more, then we can only assume that the steps
                    # all failed
                    if master_build:
                        build.updateFromBBBuild(session, master_build)
                    else:
                        # TODO: Run this as an update query where build_id = ?, step.endtime = NULL
                        for step in build.steps:
                            if step.endtime is None:
                                step.endtime = end_time
                                step.status = FAILURE

            # On a reconfig/restart we want to:
            # - Find a list of all pending requests
            # - Match them up with existing entries in the db
            # - Add them into self.request_mapper if they match
            # On a restart, none of the builders will have pending
            # requests, so we won't find any to store in self.request_mapper
            for builderName in self.status.getBuilderNames():
                master_builder = self.status.getBuilder(builderName)
                db_builder = model.Builder.get(session, builderName, self.master_id)
                for p in master_builder.getPendingBuilds():
                    r = model.Request.get(session, builder=db_builder,
                            submittime=datetime.utcfromtimestamp(p.getSubmitTime()),
                            source=p.source)
                    if r:
                        log.msg("DBMSG: Found matching request for db request %i" % r.id)
                        self.request_mapping[p] = r

            # Go though and mark any requests which don't have an entry in
            # request_mapping as lost This means that they were never built,
            # and aren't in any builder's list of pending builds, so they will
            # never be built.
            known_requests = self.request_mapping.values()
            for req in session.query(model.Request).filter_by(startcount=0, lost=False).join(model.Builder).filter_by(master_id=self.master_id):
                if req not in known_requests:
                    log.msg("DBMSG: Marking request %i as lost and gone forever" % req.id)
                    req.lost = True

            session.commit()
            self.status.subscribe(self)
        except:
            log.msg("DBERROR: Couldn't setup master")
            log.err()
        finally:
            session.close()

    def builderAdded(self, name, builder):
        session = self.Session()
        try:
            b = model.Builder.get(session, name, self.master_id)
            b.category = builder.category
            self.builders.append(builder)

            db_slaves = set()
            db_slaves_by_name = {}
            for builder_slave in b.slaves:
                db_slaves.add(builder_slave.slave.name)
                db_slaves_by_name[builder_slave.slave.name] = builder_slave

            bb_slaves = set(s for s in builder.slavenames)

            # Which slaves were added to this builder
            new_slaves = bb_slaves - db_slaves
            # Which slaves were removed from this builder
            old_slaves = db_slaves - bb_slaves

            for s in new_slaves:
                bs = model.BuilderSlave(added=datetime.now(), slave=model.Slave.get(session, s))
                b.slaves.append(bs)
                session.add(bs)

            for s in b.slaves[:]:
                if s.slave.name in old_slaves:
                    # Mark it as removed
                    db_slaves_by_name[s.slave.name].removed = datetime.now()

            session.commit()

            # Subscribe to all builds that are currently in progress
            for build in builder.currentBuilds:
                log.msg("DBMSG: Attaching to %s %s" % (name, build))
                db_build = session.query(model.Build).filter_by(buildnumber=build.number, builder_id=b.id, endtime=None).first()
                if not db_build:
                    continue
                db_build.updateFromBBBuild(session, build)
                status = DBBuildStatus(db_build.id, self.subscribers)
                build.subscribe(status)
                d = build.waitUntilFinished()
                d.addCallback(lambda s: s.unsubscribe(status))
            return self
        except:
            log.msg("DBERROR: Couldn't add builder %s" % name)
            log.err()
        finally:
            session.close()


    def buildStarted(self, builderName, build):
        session = self.Session()
        try:
            b = model.Build.fromBBBuild(session, build, builderName,
                    self.master_id,
                    self.request_mapping)

            for s in build.steps:
                b.steps.append(model.Step(name=s.name, description=s.text))

            session.commit()
            for sub in self.subscribers:
                if hasattr(sub, 'buildStarted'):
                    try:
                        sub.buildStarted(builderName, build, b)
                    except:
                        log.msg("DBERROR: Couldn't notify subscriber %s of build starting" % sub)
                        log.err()
            return DBBuildStatus(b.id, self.subscribers)
        except:
            log.msg("DBERROR: Couldn't start build %s on builder %s" % (build.number, builderName))
            log.err()
        finally:
            session.close()

    def buildFinished(self, builderName, build, results):
        session = self.Session()
        try:
            builder = model.Builder.get(session, builderName, self.master_id)

            b = session.query(model.Build).filter_by(buildnumber=build.number, builder_id=builder.id, endtime=None).first()
            # This build may not exist yet in the database.  This can happen if
            # the DB status plugin isn't active when the build started.  If we
            # can't find the build in the database, we should create it.
            if not b:
                b = model.Build.fromBBBuild(session, build, builderName,
                        self.master_id, self.request_mapping)

            finished = datetime.utcfromtimestamp(build.finished)
            b.endtime = finished
            b.result = results

            # Add the properties
            b.properties = model.Property.fromBBProperties(session, build.getProperties())

            session.commit()
            for sub in self.subscribers:
                if hasattr(sub, 'buildFinished'):
                    try:
                        sub.buildFinished(builderName, build, results, b)
                    except:
                        log.msg("DBERROR: Couldn't notify subscriber %s of build finishing" % sub)
                        log.err()
        except:
            log.msg("DBERROR: Couldn't stop build %s on builder %s" % (build.number, builderName))
            log.err()
        finally:
            session.close()

    def requestSubmitted(self, request):
        session = self.Session()
        try:
            # Add any new request into the database, as well as into our
            # internal mapping of build requests to database objects
            builder = model.Builder.get(session, request.builderName, self.master_id)
            r = model.Request.fromBBRequest(session, builder, request)
            session.add(r)
            session.commit()
            self.request_mapping[request] = r
            log.msg("DBMSG: Mapping %i requests" % len(self.request_mapping))
        except:
            log.msg("DBERROR: Couldn't record new request on builder %s" % request.builderName)
            log.err()
        finally:
            session.close()

    def requestCancelled(self, builder, request):
        if request in self.request_mapping:
            try:
                # This request was cancelled by a user via the web interface
                # We need to mark it as cancelled in the database as well
                session = self.Session()
                req = session.merge(self.request_mapping[request])
                req.cancelled = True
                session.commit()
                del self.request_mapping[request]
            except:
                log.msg("DBERROR: Couldn't cancel request %s" % req.id)
                log.err()
            finally:
                session.close()

        else:
            log.msg("DBERROR: Couldn't cancel unmapped request")

    def slaveConnected(self, slaveName):
        session = self.Session()
        try:
            model.MasterSlave.setConnected(session, self.master_id, slaveName)
            session.commit()
        except:
            log.msg("DBERROR: Couldn't mark slave %s as connected" % slaveName)
            log.err()
        finally:
            session.close()

    def slaveDisconnected(self, slaveName):
        session = self.Session()
        try:
            model.MasterSlave.setDisconnected(session, self.master_id, slaveName)
            session.commit()
        except:
            log.msg("DBERROR: Couldn't mark slave %s as disconnected" % slaveName)
            log.err()
        finally:
            session.close()
