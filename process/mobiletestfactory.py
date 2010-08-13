import re

from buildbot.process.factory import BuildFactory
from buildbot.steps.shell import Compile, ShellCommand, WithProperties, \
  SetProperty
from buildbot.changes import base, changes

import buildbotcustom.steps.misc
import buildbotcustom.steps.talos
import buildbotcustom.steps.mobile
reload(buildbotcustom.steps.misc)
reload(buildbotcustom.steps.talos)
reload(buildbotcustom.steps.mobile)
from buildbotcustom.steps.misc import FindFile, DownloadFile, UnpackFile, \
  DisconnectStep
from buildbotcustom.steps.talos import MozillaRunPerfTests
from buildbotcustom.steps.mobile import MobileParseTestLog

import buildbotcustom.steps.unittest
reload(buildbotcustom.steps.unittest)
from buildbotcustom.steps.unittest import ShellCommandReportTimeout, \
  emphasizeFailureText, summaryText

#Parse a line of output from .ini files
def parse_build_info(output, repo_type):
    retval = {}
    patterns=["^BuildID=(?P<%s_buildid>.*)$" % repo_type,
              "^SourceStamp=(?P<%s_changeset>.*)$" % repo_type,
              "^SourceRepository=(?P<%s_repository>\S+)$" % repo_type,
              "^Milestone=(?P<milestone>.*)$",
              "^Version=(?P<version>.*)$",
             ]

    for pattern in patterns:
        m = re.search(pattern, output, re.M)
        if m:
            retval.update(m.groupdict())
    return retval

def download_dir(rc, stdout, stderr):
    m = re.search("^(?P<download_dir>.*/)[^/]*$", stdout)
    return m.groupdict()

def simpleCVS(cvsroot, module, path, workdir):
    #TODO: Add ability to pull up to a certain date (-D)
    return ShellCommand(
               command=['cvs', '-d', cvsroot, 'co', '-d', module, path],
               description=[module, 'checkout'],
               name="%s_checkout" % module,
               workdir=workdir,
               haltOnFailure=True,
           )

def simpleHG(host, repo, workdir, target=None):
    if target is None:
        target = repo.rstrip('/').split('/')[-1]
    steps = []
    steps.append(ShellCommand(
        command=['rm', '-rf', target],
        workdir=workdir,
        description=['delete', target, 'repo'],
        name='rm_%s_repo' % target,
    ))
    steps.append(ShellCommand(
        command=['hg', '-v', 'clone', '%s/%s' % (host, repo), target],
        workdir=workdir,
        description=[target, 'clone'],
        name='clone_%s' % target,
        haltOnFailure=True,
    ))
    return steps


#
# Run Talos on Maemo Phones
#
class MobileTalosFactory(BuildFactory):

    def __init__(self, test, timeout, branch=None, talos_config_file='sample.config',
                 results_server=None, reboot=True, base_dir='/builds',
                 reboot_cmd=['sudo', 'reboot-user'], nochrome=False,
                 cvsroot=":pserver:anonymous@63.245.209.14:/cvsroot",
                 hg_host='http://hg.mozilla.org', tools_repo_path='build/tools',
                 talos_tarball=None, pageloader_tarball=None,
                 cleanup_glob='tools talos fennec* *.tar.bz2 *.zip',
                 tp4_source='/tools/tp4', browser_wait=7, **kwargs):
        BuildFactory.__init__(self, **kwargs)
        self.test = test
        self.timeout = timeout
        self.branch = branch
        self.talos_config_file = talos_config_file
        self.results_server = results_server
        self.reboot = reboot
        self.base_dir = base_dir
        self.reboot_cmd = reboot_cmd
        self.nochrome = '' if nochrome is None else '--noChrome'
        self.cvsroot = cvsroot #We are using a static ip because of dns failures
        self.hg_host = hg_host
        self.tools_repo_path = tools_repo_path
        self.talos_tarball = talos_tarball
        self.pageloader_tarball = pageloader_tarball
        self.cleanup_glob = cleanup_glob
        self.tp4_source = tp4_source
        self.browser_wait = browser_wait

        self.addStartupSteps()
        self.addCleanupSteps()
        self.addSetupSteps()
        self.addObtainBuildSteps()
        self.addRunSteps()
        self.addFinalSteps()

    def addStartupSteps(self):
        self.addStep(ShellCommand(
            command=['echo', WithProperties("TinderboxPrint: %(slavename)s")],
            description="hostname",
            name='hostname'
        ))
        self.addStep(ShellCommand(
            command=['sh', '-c', 'echo TinderboxPrint: $(uname -m)'],
            description='arch',
            name='arch',
        ))
        self.addStep(ShellCommand(
            command=['free'],
            workdir=self.base_dir,
            description=['memory', 'check'],
            name='memory_check',
        ))
        self.addStep(ShellCommand(
            command=['df', '-h'],
            workdir=self.base_dir,
            description=['check', 'disk'],
            name='disk_check',
         ))

    def addCleanupSteps(self):
        self.addStep(ShellCommand(
            command=['sh', '-c',
                     "rm talos/tp4 ; rm -rf %s" % self.cleanup_glob],
            # If you don't delete the tp4 symlink before cleanup
            # bad things happen!
            workdir=self.base_dir,
            name='cleanup',
            description='cleanup',
            haltOnFailure=True,
        ))

    def addObtainBuildSteps(self):
        self.addDownloadBuildStep()
        self.addUnpackBuildSteps()

        #If people *hate* lambdas, i could define simple functions
        mozilla_fn = lambda x,y,z: parse_build_info(' '.join([y,z]), 'mozilla')
        mobile_fn = lambda x,y,z: parse_build_info(' '.join([y,z]), 'mobile')
        self.addStep(SetProperty(
            command=['cat', 'platform.ini'],
            workdir='%s/fennec' % self.base_dir,
            extract_fn=mozilla_fn,
            description=['get', 'mobile', 'revision'],
            name='mozilla_rev',
        ))
        self.addStep(SetProperty(
            command=['cat', 'application.ini'],
            workdir='%s/fennec' % self.base_dir,
            extract_fn=mobile_fn,
            description=['get', 'mobile', 'revision'],
            name='mobile_rev',
        ))
        self.addStep(SetProperty(
            command=['echo',
                     WithProperties('%(mozilla_changeset)s:%(mobile_changeset)s')],
            property='got_revision',
            name='set_got_revision',
            description=['set_got_revision']
        ))
        self.addStep(ShellCommand(
            command=['echo', 'TinderboxPrint:',
                     WithProperties('<a href=%(mozilla_repository)s/rev/%(mozilla_changeset)s' +
                                    'title="Built from Mozilla revision %(mozilla_changeset)s">' +
                                    'moz:%(mozilla_changeset)s</a> <br />' +
                                    '<a href=%(mobile_repository)s/rev/%(mobile_changeset)s' +
                                    'title="Built from Mobile revision %(mobile_changeset)s">' +
                                    'mobile:%(mobile_changeset)s</a>')],
            description=['list', 'revisions'],
            name='rev_info',
        ))

    def addSetupSteps(self):
        if self.talos_tarball is None:
            self.addStep(simpleCVS(cvsroot=self.cvsroot, module='talos',
                                   path='mozilla/testing/performance/talos',
                                   workdir=self.base_dir))
        else:
            self.addTarballTalosSteps()
        if self.pageloader_tarball is None:
            self.addHGPageloaderSteps()
        else:
            self.addTarballPageloaderSteps()
        self.addStep(ShellCommand(
            command=['sudo', 'chmod', '-R', '+rx', '.'],
            workdir=self.base_dir,
            description=['fix', 'permissions'],
            name='fix_perms',
        ))
        if 'tp4' in self.test:
            self.addStep(ShellCommand(
                #maybe do rsync -a self.tp4_source.rstrip('/')
                #                  self.base_dir/talos/page_load_test+'/'
                #so we know we are always working with a pristine tp4 set
                command=['ln', '-s', self.tp4_source, '.'],
                workdir='%s/talos/page_load_test' % self.base_dir,
                description=['create', 'tp4', 'symlink'],
                name='tp4_symlink',
                haltOnFailure=True,
            ))

    def addHGPageloaderSteps(self):
        self.addSteps(simpleHG(self.hg_host, self.tools_repo_path,
                               self.base_dir, 'tools'))

    def addTarballPageloaderSteps(self):
        self.addStep(ShellCommand(
            command=['wget', self.pageloader_tarball,
                     '-O', 'pageloader.tar.bz2'],
            workdir=self.base_dir,
            #PerfConfigurator.py will copy the pageloader extension
            description=['get', 'pageloader'],
            name='get_pageloader',
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            command=['tar', 'jxvf', '%s/pageloader.tar.bz2' % self.base_dir],
            workdir='%s/fennec' % self.base_dir,
            description=['unpack', 'pageloader'],
            name='unpack_pageloader',
            haltOnFailure=True,
        ))

    def addTarballTalosSteps(self):
        self.addStep(ShellCommand(
            command=['wget', self.talos_tarball, '-O', 'talos.tar.bz2'],
            workdir=self.base_dir,
            description=['get', 'talos'],
            name='get_talos',
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            command=['tar', 'jxvf', 'talos.tar.bz2'],
            workdir=self.base_dir,
            description=['unpack', 'talos'],
            name='unpack_talos',
            haltOnFailure=True,
        ))

    def addRunSteps(self):
        perfconf_cmd=['python',
                      "PerfConfigurator.py",
                      '-v', '-e',
                      '%s/fennec/fennec' % self.base_dir,
                      '-t', WithProperties("%(slavename)s"),
                      '--branch', self.branch,
                      '--branchName', self.branch,
                      '--activeTests', self.test,
                      '--sampleConfig', self.talos_config_file,
                      '--browserWait', str(self.browser_wait),
                      '--resultsServer', self.results_server,
                      self.nochrome,
                      '--resultsLink', '/server/collect.cgi',
                      '--output', 'local.config']
        runtest_cmd=["python", "run_tests.py", "--noisy",
                     "--debug", "local.config"]
        self.addStep(ShellCommand(
            command=perfconf_cmd,
            workdir="%s/talos" % self.base_dir,
            description=['perfconfig', self.test],
            haltOnFailure=True,
            name='perfconfig_%s' % self.test,
        ))
        self.addStep(ShellCommand(
            command=["cat", "local.config"],
            workdir="%s/talos" % self.base_dir,
            description=["config", self.test],
            name='cat_config_%s' % self.test,
            haltOnFailure=True,
        ))
        self.addStep(MozillaRunPerfTests(
            command=runtest_cmd,
            workdir = "%s/talos" % self.base_dir,
            description=['run', self.test],
            timeout=self.timeout + 2*60, #make sure that can hit the test
                                         #timeout before we kill the command
            warnOnWarnings=True,
            haltOnFailure=False,
            name='run_test_%s' % self.test,
        ))

    def addFinalSteps(self):
        if self.reboot:
            self.addStep(DisconnectStep(
                command=self.reboot_cmd,
                force_disconnect=True,
                warnOnFailure=False,
                flunkOnFailure=False,
                alwaysRun=True,
                description='reboot',
                name='reboot',
            ))
        else:
            self.addCleanupSteps()

    # from TalosFactory
    def addDownloadBuildStep(self):
        def get_url(build):
            url = build.source.changes[-1].files[0]
            return url
        self.addStep(DownloadFile(
            url_fn=get_url,
            url_property="fileURL",
            filename_property="filename",
            workdir=self.base_dir,
            haltOnFailure=True,
            timeout=60*60,
            name="get_build",
            description=['get', 'build'],
        ))

    # also from TalosFactory. Moving towards refactoring, which is good,
    # but not there yet.
    def addUnpackBuildSteps(self):
        self.addStep(UnpackFile(
            filename=WithProperties("%(filename)s"),
            workdir=self.base_dir,
            haltOnFailure=True,
            timeout=60*60,
            name="unpack_build",
            description=['unpack', 'build'],
        ))

#
# Run unit tests through maemkit on Maemo Phones
#
class MobileUnittestFactory(MobileTalosFactory):
    def __init__(self, test_type, known_fail_count=0, clients=None,
                 maemkit_repo_path='qa/maemkit', maemkit_tarball=None,
                 **kwargs):
        ''' clients is a tuple of (clientnumber, totalclients), e.g. (1,4).
        being hopeful about default 0 for known fail count'''
        self.test_type = test_type
        self.known_fail_count = known_fail_count
        self.clients = clients
        self.maemkit_repo_path = maemkit_repo_path
        self.maemkit_tarball = maemkit_tarball
        MobileTalosFactory.__init__(self, **kwargs)

    def addSetupSteps(self):
        self.addStep(ShellCommand(
            command=['mkdir', '-p', '%s/maemkit_logs' % self.base_dir],
            name='mkdir_maemkit_logs',
            description=['make', 'maemkit', 'log', 'dir'],
        ))
        if self.maemkit_tarball is not None:
            self.addTarballMaemkitSteps()
        else:
            self.addSteps(simpleHG(self.hg_host,
                          self.maemkit_repo_path, self.base_dir, 'maemkit'))

    def addObtainBuildSteps(self):
        MobileTalosFactory.addObtainBuildSteps(self)
        self.addStep(SetProperty(
            command=['echo', WithProperties("%(fileURL)s")],
            extract_fn=download_dir,
            name='download_dir',
            description=['download', 'directory', 'property'],
        ))
        self.addStep(ShellCommand(
            command=['wget',
                     WithProperties("%(download_dir)s/fennec-%(version)s.en-US.linux-gnueabi-arm.tests.zip"),
                     '-O', 'fennec-tests.zip'],
            workdir=self.base_dir,
            haltOnFailure=True,
            name="get_tests",
            description=['get', 'unit', 'tests'],
        ))
        self.addStep(ShellCommand(
            command=['unzip', '%s/fennec-tests.zip' % self.base_dir],
            workdir="%s/fennec" % self.base_dir,
            name='unpack_test',
            description=['unpack', 'tests'],
            haltOnFailure=True,
        ))

    def addTarballMaemkitSteps(self):
        self.addStep(ShellCommand(
            command=['wget', self.maemkit_tarball,
                     '-O', 'maemkit.tar.bz2'],
            workdir=self.base_dir,
            description=['get', 'maemkit'],
            name='get_maemkit',
            haltOnFailure=True,
        ))
        self.addStep(ShellCommand(
            command=['tar', 'jxvf', 'maemkit.tar.bz2'],
            workdir='%s' % self.base_dir,
            description=['unpack', 'maemkit'],
            name='unpack_maemkit',
            haltOnFailure=True,
        ))

    def addRunSteps(self):
        test_command = ['python', 'maemkit-chunked.py']
        log_name = 'log_%s.txt' % self.test_type
        test_command.append('--testtype=%s' % self.test_type)
        if self.clients:
            assert issubclass(type(self.clients), tuple), "self.clients must be tuple"
            assert len(self.clients) is 2, "self.clients too long"
            client, total = self.clients
            test_command.append('--client-number=%d' % client)
            test_command.append('--total-clients=%d' % total)
        self.addStep(ShellCommand(
            command=test_command,
            workdir="%s/maemkit" % self.base_dir,
            description=['run', self.test],
            name="run_%s" % self.test,
            haltOnFailure=True,
            timeout=self.timeout,
        ))
        self.addStep(MobileParseTestLog,
            name=self.test_type,
            command=['cat', log_name],
            knownFailCount=self.known_fail_count,
            workdir="/builds/fennec",
            description=['parse', self.test_type, 'log'],
            timeout=120,
            flunkOnFailure=False,
            haltOnFailure=False,
       )
