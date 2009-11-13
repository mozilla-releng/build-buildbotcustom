import datetime
import sqlalchemy
from sqlalchemy import Column, Integer, String, Unicode, UnicodeText, \
        Boolean, Text, DateTime, ForeignKey, Table, UniqueConstraint, \
        and_, or_
from sqlalchemy.orm import sessionmaker, relation, mapper, eagerload
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.orderinglist import ordering_list
from jsoncol import JSONColumn

from twisted.python import log

Base = declarative_base()
metadata = Base.metadata
Session = None

def connect(url, drop_all=False, **kwargs):
    Base.metadata.bind = sqlalchemy.create_engine(url, **kwargs)
    if drop_all:
        log.msg("DBMSG: Warning, dropping all tables")
        Base.metadata.drop_all()
    Base.metadata.create_all()
    global Session
    Session = sqlalchemy.orm.sessionmaker(bind=Base.metadata.bind)
    return Session

file_changes = Table('file_changes', Base.metadata,
    Column('file_id', Integer, ForeignKey('files.id'), nullable=False, index=True),
    Column('change_id', Integer, ForeignKey('changes.id'), nullable=False, index=True),
    )

build_properties = Table('build_properties', Base.metadata,
    Column('property_id', Integer, ForeignKey('properties.id'), nullable=False, index=True),
    Column('build_id', Integer, ForeignKey('builds.id'), nullable=False, index=True),
    )

request_properties = Table('request_properties', Base.metadata,
    Column('property_id', Integer, ForeignKey('properties.id'), nullable=False, index=True),
    Column('request_id', Integer, ForeignKey('requests.id'), nullable=False, index=True),
    )

# TODO: track ordering?
build_requests = Table('build_requests', Base.metadata,
    Column('build_id', Integer, ForeignKey('builds.id'), nullable=False, index=True),
    Column('request_id', Integer, ForeignKey('requests.id'), nullable=False, index=True),
    )

class File(Base):
    __tablename__ = "files"
    id = Column(Integer, primary_key=True)
    path = Column(Unicode(400), index=True, nullable=False)

    @classmethod
    def get(cls, session, path):
        """Retrieve a File object given its path.  If the path doesn't exist
        yet in the database, it is created and added to the session, but not
        committed."""
        path = unicode(path)
        f = session.query(cls).filter_by(path=path).first()
        if not f:
            f = cls(path=path)
            session.add(f)
        return f

class Property(Base):
    __tablename__ = "properties"
    id = Column(Integer, primary_key=True)
    name = Column(Unicode(40), index=True)
    source = Column(Unicode(40), index=True)
    value = Column(JSONColumn, nullable=True)

    @staticmethod
    def equals(dbprops, bbprops):
        """Returns True if the list of database Property objects `dbprops`
        matches the buildbot Properties `bbprops`"""
        dbprops = sorted((p.name, p.value, p.source) for p in dbprops.properties)
        bbprops = sorted((p[0], p[1], p[2]) for p in bbprops.asList())
        return dbprops == bbprops

    @classmethod
    def get(cls, session, name, source, value):
        """Retrieve the Property for the given name, source, and value.  If the
        property doesn't exist, it will be created and added to the session,
        but not committed."""
        name = unicode(name)
        source = unicode(source)
        p = session.query(cls).filter_by(name=name, source=source, value=value).first()
        if not p:
            p = cls(name=name, source=source, value=value)
            session.add(p)
        return p

    @classmethod
    def fromBBProperties(cls, session, props):
        """Return a list of Property objects that reflect a buildbot Properties
        object."""
        names = [unicode(p[0]) for p in props.asList()]
        values = [p[1] for p in props.asList()]
        sources = [unicode(p[2]) for p in props.asList()]
        all = session.query(cls).filter(cls.name.in_(names)).filter(sqlalchemy.or_(cls.value.in_(values), cls.value == None)).filter(cls.source.in_(sources)).all()

        retval = []
        for prop in all:
            if props[prop.name] == prop.value and props.getPropertySource(prop.name) == prop.source:
                retval.append(prop)

        new_props = set(names) - set([p.name for p in retval])
        for name in new_props:
            p = cls(name=unicode(name), value=props[name],
                    source=unicode(props.getPropertySource(name)))
            retval.append(p)
        return retval

class Master(Base):
    __tablename__ = "masters"
    id = Column(Integer, primary_key=True)
    url = Column(Unicode(50), index=True, nullable=False, unique=True)
    name = Column(Unicode(100), nullable=True)

    @classmethod
    def get(cls, session, url):
        master = session.query(cls).filter_by(url=unicode(url)).first()
        if not master:
            master = cls(url=unicode(url))
            session.add(master)
        return master

class MasterSlave(Base):
    __tablename__ = "master_slaves"
    id = Column(Integer, primary_key=True)
    slave_id = Column(Integer, ForeignKey('slaves.id'), nullable=False, index=True)
    slave = relation("Slave")
    master_id = Column(Integer, ForeignKey(Master.id), nullable=False, index=True)
    master = relation(Master)
    connected = Column(DateTime, nullable=False, index=True)
    disconnected = Column(DateTime, nullable=True, index=True)

    @classmethod
    def setConnected(cls, session, master_id, name, t=None):
        slave = Slave.get(session, name)
        s = session.query(cls).filter_by(slave=slave, master_id=master_id, disconnected=None).first()
        if not s:
            s = cls(slave_id=slave.id, master_id=master_id)
            session.add(s)
        if not s.connected:
            if not t:
                t = datetime.datetime.now()
            s.connected = t

    @classmethod
    def setDisconnected(cls, session, master_id, name, t=None):
        slave = Slave.get(session, name)
        s = session.query(cls).filter_by(slave=slave, master_id=master_id, disconnected=None).first()
        if not s:
            raise ValueError("So such slave")
        if not s.connected:
            raise ValueError("Slave isn't connected")

        if not t:
            t = datetime.datetime.now()
        s.disconnected = t

class Slave(Base):
    __tablename__ = "slaves"
    id = Column(Integer, primary_key=True)
    name = Column(Unicode(50), index=True, nullable=False)

    @classmethod
    def get(cls, session, name):
        """Retrieve the Slave with the given name.  If the slave doesn't exist,
        it will be created and added to the session, but not committed."""
        name = unicode(name)
        s = session.query(cls).filter_by(name=name).first()
        if not s:
            s = cls(name=name)
            session.add(s)
        return s

class BuilderSlave(Base):
    __tablename__ = "builder_slaves"
    id = Column(Integer, primary_key=True)
    builder_id = Column(Integer, ForeignKey('builders.id'), nullable=False, index=True)
    builder = relation("Builder")
    slave_id = Column(Integer, ForeignKey('slaves.id'), nullable=False, index=True)
    slave = relation(Slave)
    added = Column(DateTime, nullable=False, index=True)
    removed = Column(DateTime, nullable=True, index=True)

class Builder(Base):
    __tablename__ = "builders"
    id = Column(Integer, primary_key=True)
    name = Column(Unicode(200), index=True, nullable=False)
    master_id = Column(Integer, ForeignKey(Master.id), nullable=False, index=True)
    master = relation(Master, backref='builders')
    category = Column(Unicode(30), index=True)
    __table_args__ = (UniqueConstraint('name', 'master_id'), {})

    @classmethod
    def get(cls, session, name, master_id):
        """Retrieve the Builder for the given name and master_id.  If the
        builder doesn't exist, it will be created and added to the session, but
        not committed."""
        name = unicode(name)
        b = session.query(cls).filter_by(name=name, master_id=master_id).first()
        if not b:
            b = cls(name=name, master_id=master_id)
            session.add(b)
        return b

Builder.slaves = relation(BuilderSlave, primaryjoin=
        and_(BuilderSlave.builder_id == Builder.id,
             BuilderSlave.removed == None))

class Change(Base):
    __tablename__ = "changes"
    id = Column(Integer, primary_key=True)
    number = Column(Integer, nullable=False)
    branch = Column(Unicode(50), nullable=True)
    revision = Column(Unicode(50), nullable=True)
    who = Column(Unicode(200), nullable=True, index=True)
    files = relation(File, secondary=file_changes)
    comments = Column(UnicodeText, nullable=True)
    when = Column(DateTime, nullable=True)

    def equals(self, bbChange):
        """Returns True if this Change refers to the same thing as a buildbot
        Change object"""
        if self.number != bbChange.number:
            return False
        if self.branch != bbChange.branch:
            return False
        if self.who != unicode(bbChange.who):
            return False
        if self.comments != unicode(bbChange.comments):
            return False
        if bbChange.when:
            if self.when != datetime.datetime.utcfromtimestamp(bbChange.when):
                return False
        elif self.when:
            return False

        myfiles = sorted(f.path for f in self.files)
        theirfiles = sorted(f for f in bbChange.files)
        if myfiles != theirfiles:
            return False
        return True

    @classmethod
    def fromBBChange(cls, session, change):
        """Return a Change database object that reflects a buildbot Change
        object object."""
        # Look for a change object in the database
        if change.when:
            when = datetime.datetime.utcfromtimestamp(change.when)
        else:
            when = None

        if change.revision is None:
            revision = None
        else:
            revision = unicode(change.revision)
        possible_changes_q = session.query(cls).filter_by(
                number=change.number,
                branch=unicode(change.branch),
                revision=revision,
                who=unicode(change.who),
                comments=unicode(change.comments),
                when=when,
                )
        possible_changes = possible_changes_q.all()

        # Look at files
        for c in possible_changes:
            files = [f.path for f in c.files]
            files.sort()
            if files != sorted([f.path for f in c.files]):
                continue
            return c

        # We didn't find an existing object in the database, so
        # let's create one
        if change.when:
            when = datetime.datetime.utcfromtimestamp(change.when)
        else:
            when = None
        c = cls(number=change.number,
                branch=unicode(change.branch),
                revision=unicode(change.revision),
                who=unicode(change.who),
                comments=unicode(change.comments),
                when=when,
                )
        c.files = [File.get(session, path) for path in change.files]
        return c

class SourceChange(Base):
    __tablename__ = "source_changes"
    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey('sourcestamps.id'), nullable=False, index=True)
    change_id = Column(Integer, ForeignKey('changes.id'), nullable=False, index=True)
    order = Column(Integer, nullable=False)
    change = relation(Change)

class Patch(Base):
    __tablename__ = "patches"
    id = Column(Integer, primary_key=True)
    patch = Column(Text, nullable=True)
    patchlevel = Column(Integer, nullable=True)

class SourceStamp(Base):
    __tablename__ = "sourcestamps"
    id = Column(Integer, primary_key=True)
    branch = Column(Unicode(50), nullable=True)
    revision = Column(Unicode(50), nullable=True)
    patch_id = Column(Integer, ForeignKey(Patch.id), nullable=True)
    patch = relation(Patch)
    changes = relation(SourceChange, order_by=SourceChange.order)

    def equals(self, bbSource):
        """Returns True if this SourceStamp refers to the same thing as a buildbot
        SourceStamp object"""
        if self.branch != bbSource.branch:
            return False
        if self.revision != bbSource.revision:
            return False

        if bbSource.patch:
            if not self.patch:
                return False
            patchlevel, patchdata = bbSource.patch
            if self.patch.patch != patchdata:
                return False
            if self.patch.patchlevel != patchlevel:
                return False
        else:
            if self.patch:
                return False

        # Check list of changes
        if len(self.changes) != len(bbSource.changes):
            return False
        for c1, c2 in zip(self.changes, bbSource.changes):
            if not c1.change.equals(c2):
                return False

        return True

    @classmethod
    def fromBBSourcestamp(cls, session, ss):
        """Return a database SourceStamp object that reflect a buildbot SourceStamp"""
        changes = [Change.fromBBChange(session, c) for c in ss.changes]
        changes = [SourceChange(change=change, order=i) for i,change in enumerate(changes)]
        if ss.patch:
            patchlevel, patchdata = ss.patch
            patch = Patch(patch=patchdata, patchlevel=patchlevel)
        else:
            patch = None
        s = session.query(cls).filter_by(branch=unicode(ss.branch),
                revision=unicode(ss.revision),
                patch=patch).first()
        # Compare list of changes
        if s:
            if sorted([c.change.id for c in s.changes]) == sorted([c.change.id for c in changes]):
                return s
            s = None
        if not s:
            s = cls(branch=unicode(ss.branch),
                    revision=unicode(ss.revision),
                    patch=patch,
                    changes=changes)
        return s

class Request(Base):
    __tablename__ = "requests"
    id = Column(Integer, primary_key=True)
    submittime = Column(DateTime, index=True)
    builder_id = Column(Integer, ForeignKey(Builder.id), index=True, nullable=True)
    builder = relation(Builder, backref='requests')
    startcount = Column(Integer, index=True, nullable=False, default=0)
    source_id = Column(Integer, ForeignKey(SourceStamp.id), nullable=True)
    source = relation(SourceStamp)
    properties = relation(Property, secondary=request_properties)
    lost = Column(Boolean, default=False, nullable=False, index=True)
    cancelled = Column(Boolean, default=False, nullable=False, index=True)

    @classmethod
    def get(cls, session, builder, submittime, source):
        """Retrieve a Request for the given builder and submittime. If the
        request doesn't exist, None is returned"""
        r = session.query(cls).filter_by(
                builder=builder,
                submittime=submittime,
                lost=False,
                cancelled=False,
                startcount=0).first()

        if not r:
            return None

        if not r.source.equals(source):
            return None

        return r

    @classmethod
    def fromBBRequest(cls, session, builder, req):
        """Create a database Request object from a buildbot Request"""
        ss = SourceStamp.fromBBSourcestamp(session, req.source)
        r = cls(
                submittime=datetime.datetime.utcfromtimestamp(req.getSubmitTime()),
                builder=builder,
                startcount=0,
                lost=False,
                cancelled=False,
                source=ss,
                )
        # TODO: Get properties from request once buildbot lets us
        return r

class Step(Base):
    __tablename__ = "steps"
    id = Column(Integer, primary_key=True)
    name = Column(String(256), nullable=False, index=True)
    description = Column(JSONColumn, nullable=True)
    build_id = Column(Integer, ForeignKey('builds.id'), index=True, nullable=False)
    order = Column(Integer, nullable=False)
    starttime = Column(DateTime)
    endtime = Column(DateTime)
    status = Column(Integer, index=True)

    @classmethod
    def get(cls, session, name, build_id):
        s = session.query(cls).filter_by(name=name, build_id=build_id).first()
        if not s:
            s = cls(name=name, build_id=build_id)
            b = session.query(Build).get(build_id)
            # Put this step after the latest completed step
            last_done = -1
            for i, step in enumerate(b.steps):
                if step.endtime:
                    last_done = i
                else:
                    break

            b.steps.insert(last_done+1, s)
            session.add(s)
        return s

class Build(Base):
    __tablename__ = "builds"
    id = Column(Integer, primary_key=True)
    buildnumber = Column(Integer, index=True, nullable=False)
    properties = relation(Property, secondary=build_properties)
    builder_id = Column(Integer, ForeignKey(Builder.id), index=True, nullable=False)
    builder = relation(Builder, backref='builds')
    slave_id = Column(Integer, ForeignKey(Slave.id), index=True, nullable=False)
    slave = relation(Slave, backref='builds')
    master_id = Column(Integer, ForeignKey(Master.id), nullable=False)
    master = relation(Master, backref='builds')
    starttime = Column(DateTime, index=True)
    endtime = Column(DateTime, index=True)
    result = Column(Integer, index=True)
    reason = Column(Unicode(500))
    requests = relation(Request, secondary=build_requests, backref='builds')
    source_id = Column(Integer, ForeignKey(SourceStamp.id), nullable=True)
    source = relation(SourceStamp)
    steps = relation(Step, order_by=Step.order, collection_class=ordering_list('order'), backref='build')
    lost = Column(Boolean, nullable=False, default=False)

    def updateFromBBBuild(self, session, build):
        self.properties = Property.fromBBProperties(session, build.getProperties())

        if build.started:
            self.starttime = datetime.datetime.utcfromtimestamp(build.started)

        if build.finished:
            finished = datetime.datetime.utcfromtimestamp(build.finished)
            self.endtime = finished
            self.result = build.results

        if build.steps:
            mysteps = dict((s.name, s) for s in self.steps)
            for i,step in enumerate(build.steps):
                s = mysteps.get(step.name)
                if not s:
                    s = Step(name=step.name, build_id=self.id)
                    self.steps.insert(i,s)
                else:
                    del mysteps[step.name]
                s.description = step.text
                s.order = i
                if isinstance(step.results, int):
                    s.status = step.results
                else:
                    s.status = step.results[0]
                # This may not be set yet
                if step.started:
                    s.starttime = datetime.datetime.utcfromtimestamp(step.started)
                if step.finished:
                    s.endtime = datetime.datetime.utcfromtimestamp(step.finished)

            # Get rid of any steps that are left over
            for s in mysteps.values():
                session.delete(s)
                self.steps.remove(s)

    @classmethod
    def fromBBBuild(cls, session, build, builderName, master_id, request_mapping=None):
        """Create a database Build object from a buildbot Build"""
        builder = Builder.get(session, builderName, master_id)
        slave = Slave.get(session, build.getSlavename())
        b = cls(buildnumber=build.number, builder=builder,
                slave=slave, master_id=master_id, reason=unicode(build.reason),
                result=build.results)
        b.source = SourceStamp.fromBBSourcestamp(session, build.getSourceStamp())

        if hasattr(build, 'getRequests'):
            for req in build.getRequests():
                r = None
                if request_mapping:
                    r = request_mapping.pop(req, None)
                if not r:
                    r = Request.fromBBRequest(session, builder, req)
                else:
                    r = session.merge(r)
                r.builder = b.builder
                r.startcount += 1
                b.requests.append(r)

        # Updates times, steps, properties
        session.add(b)
        b.updateFromBBBuild(session, build)

        return b
