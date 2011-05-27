#!/usr/bin/python
"""%prog [options] host builder_path build_number

Uploads logs from build to the given host.
"""
import sys, os, cPickle, gzip, subprocess, time

from buildbot import util
from buildbot.status.builder import Results

from buildbotcustom.process.factory import postUploadCmdPrefix

def ssh(user, identity, host, remote_cmd, port=22):
    devnull = open(os.devnull)
    cmd = ['ssh', '-l', user]
    if identity:
        cmd.extend(['-i', identity])
    cmd.extend(['-p', str(port), host, remote_cmd])

    proc = subprocess.Popen(cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=devnull,)

    retcode = proc.wait()
    output = proc.stdout.read().strip()
    if retcode != 0:
        raise Exception("Command %s returned non-zero exit code %i:\n%s" % (
            cmd, retcode, output))
    return output

def scp(user, identity, host, files, remote_dir, port=22):
    devnull = open(os.devnull)
    cmd = ['scp']
    if identity:
        cmd.extend(['-i', identity])
    cmd.extend(['-P', str(port)])
    cmd.extend(files)
    cmd.append("%s@%s:%s" % (user, host, remote_dir))

    proc = subprocess.Popen(cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=devnull,)

    retcode = proc.wait()
    output = proc.stdout.read().strip()
    if retcode != 0:
        raise Exception("Command %s returned non-zero exit code %i:\n%s" % (
            cmd, retcode, output))
    return output

def getBuild(builder_path, build_number):
    build_path = os.path.join(builder_path, build_number)

    if not os.path.exists(build_path):
        raise ValueError("Couldn't find %s" % build_path)

    class FakeBuilder:
        basedir = builder_path
        name = os.path.basename(builder_path)

    build = cPickle.load(open(build_path))
    build.builder = FakeBuilder()
    return build

def getAuthor(build):
    props = build.getProperties()
    if 'who' in props:
        return props['who']

    changes = build.getSourceStamp().changes
    if changes:
        return changes[0].who

def getBuildId(build):
    try:
        return build.getProperty('buildid')
    except:
        return None

def isNightly(build):
    try:
        if build.getProperty('nightly_build'):
            return True
    except:
        return False

def formatLog(tmpdir, build, builder_suffix=''):
    """
    Returns a filename with the contents of the build log
    written to it.
    """
    builder_name = build.builder.name
    build_name = "%s%s-build%s.txt.gz" % (builder_name, builder_suffix, build_number)

    logFile = gzip.GzipFile(os.path.join(tmpdir, build_name), "w")

    # Header information
    logFile.write("builder: %s\n" % builder_name)
    logFile.write("slave: %s\n" % build.getSlavename())
    logFile.write("starttime: %s\n" % build.started)

    results = build.getResults()
    try:
        results_str = Results[results]
    except:
        results_str = "Unknown"
    logFile.write("results: %s (%s)\n" % (results_str, results))

    props = build.getProperties()
    if props.getProperty('buildid') is not None:
        logFile.write("buildid: %s\n" % props['buildid'])

    if props.getProperty('builduid') is not None:
        logFile.write("builduid: %s\n" % props['builduid'])

    if props.getProperty('got_revision') is not None:
        logFile.write("revision: %s\n" % props['got_revision'])
    elif props.getProperty('revision') is not None:
        logFile.write("revision: %s\n" % props['revision'])

    logFile.write("\n")


    # Steps
    for step in build.getSteps():
        times = step.getTimes()
        if not times or not times[0]:
            elapsed = "not started"
        elif not times[1]:
            elapsed = "not started"
        else:
            elapsed = util.formatInterval(times[1] - times[0])

        results = step.getResults()[0]
        if results == (None, []):
            results = "not started"

        shortText = ' '.join(step.getText()) + ' (results: %s, elapsed: %s)' % (results, elapsed)
        logFile.write("========= Started %s ==========\n" % shortText)

        for log in step.getLogs():
            data = log.getTextWithHeaders()
            logFile.write(data)
            if not data.endswith("\n"):
                logFile.write("\n")

        logFile.write("======== Finished %s ========\n\n" % shortText)
    logFile.close()
    return os.path.join(tmpdir, build_name)

if __name__ == "__main__":
    from optparse import OptionParser
    import tempfile, shutil

    parser = OptionParser(__doc__)
    parser.set_defaults(
            nightly=False,
            release=None,
            trybuild=False,
            shadowbuild=False,
            l10n=False,
            user=os.environ.get("USER"),
            product="firefox",
            )
    parser.add_option("-u", "--user", dest="user", help="upload user name")
    parser.add_option("-i", "--identity", dest="identity", help="ssh identity")
    parser.add_option("-b", "--branch", dest="branch", help="branch")
    parser.add_option("-p", "--platform", dest="platform", help="platform")
    parser.add_option("--product", dest="product", help="product directory")
    parser.add_option("--nightly", dest="nightly", action="store_true",
            help="upload to nightly dir")
    parser.add_option("--release", dest="release",
            help="upload to release candidates dir")
    parser.add_option("--l10n", dest="l10n", action="store_true",
            help="include locale value in log filename")
    parser.add_option("--try", dest="trybuild", action="store_true",
            help="upload to try build directory")
    parser.add_option("--shadow", dest="shadowbuild", action="store_true",
            help="upload to shadow build directory")

    options, args = parser.parse_args()

    if not options.branch:
        parser.error("branch required")

    if not options.platform and not options.release:
        parser.error("platform required")

    if len(args) != 3:
        parser.error("Need to specify host, builder_path and build number")

    host, builder_path, build_number = args

    local_tmpdir = tempfile.mkdtemp()

    try:
        # Format the log into a compressed text file
        build = getBuild(builder_path, build_number)
        if options.l10n:
            suffix = '-%s' % build.getProperty('locale')
            logfile = formatLog(local_tmpdir, build, suffix)
        else:
            logfile = formatLog(local_tmpdir, build)

        # Now....upload it!
        remote_tmpdir = ssh(user=options.user, identity=options.identity, host=host,
                remote_cmd="mktemp -d")
        try:
            # Release logs go into the 'logs' directory
            if options.release:
                # Create the logs directory
                ssh(user=options.user, identity=options.identity, host=host,
                        remote_cmd="mkdir %s/logs" % remote_tmpdir)
                scp(user=options.user, identity=options.identity, host=host,
                        files=[logfile], remote_dir='%s/logs' % remote_tmpdir)
                remote_files = [os.path.join(remote_tmpdir, 'logs', os.path.basename(f)) for f in [logfile]]
            else:
                scp(user=options.user, identity=options.identity, host=host,
                        files=[logfile], remote_dir=remote_tmpdir)

                remote_files = [os.path.join(remote_tmpdir, os.path.basename(f)) for f in [logfile]]

            uploadArgs = dict(
                branch=options.branch,
                product=options.product,
            )

            # Make sure debug platforms are properly identified
            # Test builders don't have the '-debug' distinction in the platform
            # string, so check in the builder name to make sure.
            platform = options.platform
            if platform:
                if '-debug' in builder_path and '-debug' not in platform:
                    platform += "-debug"

            if options.trybuild:
                uploadArgs.update(dict(
                    to_try=True,
                    to_tinderbox_dated=False,
                    who=getAuthor(build),
                    revision=build.getProperty('revision')[:12],
                    builddir="%s-%s" % (options.branch, platform),
                    ))
            else:
                buildid = getBuildId(build)

                if options.release:
                    uploadArgs['to_candidates'] = True
                    version, buildNumber = options.release.split('/')
                    uploadArgs['version'] = version
                    uploadArgs['buildNumber'] = buildNumber
                elif options.l10n:
                    uploadArgs['branch'] += '-l10n'
                    if options.nightly:
                        uploadArgs['to_tinderbox_dated'] = False
                        uploadArgs['to_dated'] = True
                        uploadArgs['to_latest'] = True
                    else:
                        uploadArgs['to_tinderbox_builds'] = True
                        uploadArgs['upload_dir'] = uploadArgs['branch']

                else:
                    uploadArgs['upload_dir'] = "%s-%s" % (options.branch, platform)
                    if buildid is None:
                        # No build id, so we don't know where to upload this :(
                        print "No build id for %s/%s, giving up" % (builder_path, build_number)
                        # Exit cleanly so we don't spam twistd.log with exceptions
                        sys.exit(0)

                    if options.nightly or isNightly(build):
                        uploadArgs['to_dated'] = True
                        # Don't upload to the latest directory for now; we have no
                        # way of purging the logs out of the latest-<branch>
                        # directories
                        #uploadArgs['to_latest'] = True
                        if 'mobile' in options.product:
                            uploadArgs['branch'] = options.branch + '-' + platform
                        else:
                            uploadArgs['branch'] = options.branch

                    if options.shadowbuild:
                        uploadArgs['to_shadow'] = True
                        uploadArgs['to_tinderbox_dated'] = False
                    else:
                        uploadArgs['to_shadow'] = False
                        uploadArgs['to_tinderbox_dated'] = True

                uploadArgs.update(dict(
                    to_try=False,
                    who=None,
                    revision=None,
                    builddir=None,
                    buildid=buildid,
                    ))
            post_upload_cmd = postUploadCmdPrefix(**uploadArgs)
            post_upload_cmd += [remote_tmpdir]
            post_upload_cmd += remote_files
            post_upload_cmd = " ".join(post_upload_cmd)

            print "Running", post_upload_cmd

            print ssh(user=options.user, identity=options.identity, host=host, remote_cmd=post_upload_cmd)
        finally:
            ssh(user=options.user, identity=options.identity, host=host,
                    remote_cmd="rm -rf %s" % remote_tmpdir)

    finally:
        shutil.rmtree(local_tmpdir)
