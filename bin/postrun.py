#!/usr/bin/env python
"""
postrun.py [options] /path/to/build/pickle

post-job tasks
- upload logs and optionally mail users about try results
- update statusdb with job info (including log url)
- send pulse message about log being uploaded

The control flow between these tasks is handled inside PostRunner.proessBuild.
After each task completes successfully, it creates a new entry in the command
queuedir adding the results of its operations as additional parameters to
postrun.py.  The command runner is then responsible for running the new command
or retrying failed commands.

For example, after the log has been uploaded and the try user mailed, processBuild creates a
new entry in the command queue copying the original arguments with the addition
of "--log-url <url>".

The statusdb import adds "--statusdb-id <buildid>".

The pulse message generation is the last task, so it doesn't create any new
command queue entries.

"""
import os, sys
import re
import cPickle as pickle
from datetime import datetime
try:
    import simplejson as json
except ImportError:
    import json

import logging
log = logging.getLogger(__name__)

import sqlalchemy as sa
import buildbotcustom.status.db.model as model
from mozilla_buildtools.queuedir import QueueDir

from util.commands import get_output

class PostRunner(object):
    def __init__(self, config):
        self.config = config

        self.command_queue = QueueDir('commands', config['command_queue'])
        self.pulse_queue = QueueDir('pulse', config['pulse_queue'])

    def uploadLog(self, build):
        """Uploads the build log, and returns the URL to it"""
        builder = build.builder

        info = self.getBuildInfo(build)
        branch = info['branch']
        product = info['product']
        platform = info['platform']

        upload_args = ['-r', '2', '-t', '10', '--master-name', self.config['statusdb.master_name']]
        if "nightly" in builder.name:
            upload_args.append("--nightly")
        if builder.name.startswith("release-"):
            upload_args.append("--release")
            upload_args.append("%s/%s" % (info.get('version'), info.get('build_number')))

        if branch == 'try':
            upload_args.append("--try")
        elif branch == 'shadow-central':
            upload_args.append("--shadow")

        if 'l10n' in builder.name:
            upload_args.append("--l10n")

        if product:
            upload_args.extend(["--product", product])

        if platform:
            upload_args.extend(["--platform", platform])
        else:
            upload_args.extend(["--platform", 'noarch'])

        if branch:
            upload_args.extend(["--branch", branch])

        upload_args.extend(self.getUploadArgs(build, product))
        upload_args.extend([builder.basedir, str(build.number)])

        my_dir = os.path.abspath(os.path.dirname(__file__))
        cmd = [sys.executable, "%s/log_uploader.py" % my_dir] + upload_args
        devnull = open(os.devnull)

        log.info("Running %s", cmd)

        output = get_output(cmd, stdin=devnull)

        # Look for URLs
        url = re.search("http(s)?://\S+", output)
        if url:
            return url.group()
        return None

    def mailResults(self, build, log_url):
        my_dir = os.path.abspath(os.path.dirname(__file__))
        cmd = [sys.executable, "%s/try_mailer.py" % my_dir,
                "--log-url", log_url,
              ]

        cmd.extend(['-f', self.config.get('mail_notifier_sender', 'tryserver@build.mozilla.org')])
        if self.config.get('mail_real_author'):
            cmd.append('--to-author')

        for r in self.config.get('mail_extra_people', []):
            cmd.extend(['-t', r])

        cmd.extend([build.builder.basedir, str(build.number)])

        # Add the command to our queue
        self.command_queue.add(json.dumps(cmd))

    def getUploadArgs(self, build, product):
        ssh_info = self.config['ssh_info']
        ssh_info = ssh_info.get(product, ssh_info["*"])

        branch = self.getBuildInfo(build).get('branch')
        ssh_info = ssh_info.get(branch, ssh_info["*"])

        retval = ['--user', ssh_info['user']]
        if 'sshkey' in ssh_info:
            retval.extend(["-i", ssh_info['sshkey']])
        retval.append(ssh_info['host'])
        return retval

    def getBuild(self, build_path):
        log.info("Loading build pickle")
        if not os.path.exists(build_path):
            raise ValueError("Couldn't find %s" % build_path)

        builder_path = os.path.dirname(build_path)
        class FakeBuilder:
            basedir = builder_path
            name = os.path.basename(builder_path)

        build = pickle.load(open(build_path))
        build.builder = FakeBuilder()
        return build

    def getBuildInfo(self, build):
        """
        Returns a dictionary with
        'branch', 'platform', 'product'
        set as appropriate
        """
        props = build.getProperties()
        retval = {}
        if props.getProperty('stage_platform') is not None:
            retval['platform'] = props['stage_platform']
        elif props.getProperty('platform') is not None:
            retval['platform'] = props['platform']
        else:
            retval['platform'] = None

        if props.getProperty('stage_product') is not None:
            retval['product'] = props['stage_product']
        elif props.getProperty('product') is not None:
            retval['product'] = props['product']
        else:
            retval['product'] = None

        if props.getProperty('branch') is not None:
            retval['branch'] = props['branch']
        else:
            retval['branch'] = None

        if props.getProperty('version') is not None:
            retval['version'] = props['version']

        if props.getProperty('build_number') is not None:
            retval['build_number'] = props['build_number']

        log.debug("Build info: %s", retval)
        return retval

    def writePulseMessage(self, options, build, build_id):
        builder_name = build.builder.name
        msg = {
                'event': 'build.%s.%s.log_uploaded' % (builder_name, build.number),
                'payload': {"build": build.asDict()},
                'master_name': options.master_name,
                'master_incarnation': options.master_incarnation,
                'id': build_id,
            }
        self.pulse_queue.add(json.dumps([msg]))

    def updateStatusDB(self, build, request_ids):
        log.info("Updating statusdb")
        session = model.connect(self.config['statusdb.url'])()
        master = model.Master.get(session, self.config['statusdb.master_url'])
        master.name = unicode(self.config['statusdb.master_name'])

        if not master.id:
            log.debug("added master")
            session.add(master)
            session.commit()

        builder_name = build.builder.name
        db_builder = model.Builder.get(session, builder_name, master.id)
        db_builder.category = unicode(build.getProperty('branch'))

        starttime = None
        if build.started:
            starttime = datetime.utcfromtimestamp(build.started)

        log.debug("searching for build")
        q = session.query(model.Build).filter_by(
                master_id=master.id,
                builder=db_builder,
                buildnumber=build.number,
                starttime=starttime,
                )
        db_build = q.first()
        if not db_build:
            log.debug("creating new build")
            db_build = model.Build.fromBBBuild(session, build, builder_name, master.id)
        else:
            log.debug("updating old build")
            db_build.updateFromBBBuild(session, build)
        session.commit()
        log.debug("committed")

        log.debug("updating schedulerdb_requests table")

        schedulerdb = sa.create_engine(self.config['schedulerdb.url'])

        for i in request_ids:
            # See if we already have this row
            q = model.schedulerdb_requests.select()
            q = q.where(model.schedulerdb_requests.c.status_build_id==db_build.id)
            q = q.where(model.schedulerdb_requests.c.scheduler_request_id==i)
            q = q.limit(1).execute()
            if not q.fetchone():
                # Find the schedulerdb build id for this
                bid = schedulerdb.execute(
                        sa.text('select id from builds where brid=:brid and number=:number'),
                        brid=i, number=build.number
                        ).fetchone()[0]
                log.debug("bid for %s is %s", i, bid)
                model.schedulerdb_requests.insert().execute(
                        status_build_id=db_build.id,
                        scheduler_request_id=i,
                        scheduler_build_id=bid,
                        )
        return db_build.id

    def processBuild(self, options, build_path, request_ids):
        build = self.getBuild(build_path)
        info = self.getBuildInfo(build)
        if not options.log_url:
            log.info("uploading log")
            log_url = self.uploadLog(build)
            if log_url is None:
                log_url = 'null'
            cmd = [sys.executable] + sys.argv + ["--log-url", log_url]
            self.command_queue.add(json.dumps(cmd))
            # If this is for try, Mail the try user as well
            if info['branch'] in self.config['mail_notifier_branches']:
                self.mailResults(build, log_url)
        elif not options.statusdb_id:
            log.info("adding to statusdb")
            log_url = options.log_url
            if log_url == 'null':
                log_url = None
            build.properties.setProperty('log_url', log_url, 'postrun.py')
            build.properties.setProperty('request_ids', [int(i) for i in request_ids], 'postrun.py')
            build_id = self.updateStatusDB(build, request_ids)

            cmd = [sys.executable] + sys.argv + ["--statusdb-id", str(build_id)]
            self.command_queue.add(json.dumps(cmd))
        else:
            log.info("publishing to pulse")
            log_url = options.log_url
            build_id = options.statusdb_id
            build.properties.setProperty('log_url', log_url, 'postrun.py')
            build.properties.setProperty('statusdb_id', build_id, 'postrun.py')
            build.properties.setProperty('request_ids', [int(i) for i in request_ids], 'postrun.py')
            self.writePulseMessage(options, build, build_id)

def main():
    from optparse import OptionParser
    parser = OptionParser()
    parser.set_defaults(
            config=None,
            loglevel=logging.INFO,
            log_url=None,
            statusdb_id=None,
            master_name=None,
            master_incarnation=None,
            )
    parser.add_option("-c", "--config", dest="config")
    parser.add_option("-v", "--verbose", dest="loglevel", const=logging.DEBUG, action="store_const")
    parser.add_option("-q", "--quiet", dest="loglevel", const=logging.WARNING, action="store_const")
    parser.add_option("--log-url", dest="log_url")
    parser.add_option("--statusdb-id", dest="statusdb_id", type="int")
    parser.add_option("--master-name", dest="master_name")
    parser.add_option("--master-incarnation", dest="master_incarnation")

    options, args = parser.parse_args()

    if not options.config:
        parser.error("you must specify a configuration file")

    logging.basicConfig(level=options.loglevel)

    config = json.load(open(options.config))

    post_runner = PostRunner(config)

    build_path, request_ids = args[0], args[1:]
    post_runner.processBuild(options, build_path, request_ids)

if __name__ == '__main__':
    main()
