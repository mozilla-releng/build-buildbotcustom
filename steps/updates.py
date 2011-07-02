from StringIO import StringIO
from os import path

import buildbot
from buildbot.interfaces import BuildSlaveTooOldError
from buildbot.process.buildstep import BuildStep
from buildbot.status.builder import SUCCESS, FAILURE, SKIPPED
from buildbot.steps.shell import WithProperties
from buildbot.steps.transfer import _FileReader, StatusRemoteCommand

class CreateUpdateSnippet(BuildStep):
    def __init__(self, objdir, milestone, baseurl, appendDatedDir=True,
                 snippetType='complete', hashType='sha512', doStepIf=True,
                 name='create_update_snippet'):
        BuildStep.__init__(self, name=name, doStepIf=doStepIf)

        self.addFactoryArguments(
          objdir=objdir,
          milestone=milestone,
          baseurl=baseurl,
          appendDatedDir=appendDatedDir
        )

        # This seems like a reasonable place to store snippets
        self.updateDir = path.join(objdir, 'dist', 'update')
        self.milestone = milestone
        self.baseurl = baseurl
        self.hashType = hashType
        self.appendDatedDir = appendDatedDir
        assert snippetType in ['complete', 'partial']
        self.snippetType = snippetType
        assert hashType in ['md5','sha1','sha256','sha384','sha512']
        self.hashType = hashType
        self.maxsize = 16384
        self.mode = None
        self.blocksize = 4096

        self.cmd = None
        self.stdio_log = None

    def _getDatedDirPath(self):
        buildid = self.getProperty('buildid')
        year   = buildid[0:4]
        month  = buildid[4:6]
        day    = buildid[6:8]
        hour   = buildid[8:10]
        minute = buildid[10:12]
        second = buildid[12:14]
        datedDir = "%s-%s-%s-%s-%s-%s-%s" % (year,
                                             month,
                                             day,
                                             hour,
                                             minute,
                                             second,
                                             self.milestone)
        return "%s/%s/%s" % (year, month, datedDir)

    def generateSnippet(self):
        # interpolate the baseurl, if necessary
        if isinstance(self.baseurl, WithProperties):
            self.baseurl = self.baseurl.render(self.build)
        # now build the URL
        downloadURL = self.baseurl + '/'
        if self.appendDatedDir:
            downloadURL += self._getDatedDirPath() + '/'
        downloadURL += self.getProperty(self.snippetType + 'MarFilename')

        # type of update (partial vs complete)
        snippet = "version=1\n"
        snippet += "type=%s\n" % self.snippetType
        # download URL
        snippet += "url=%s\n" % (downloadURL)
        # hash type
        snippet += "hashFunction=%s\n" % self.hashType
        # hash of mar
        snippet += "hashValue=%s\n" % self.getProperty(self.snippetType + 'MarHash')
        # size (bytes) of mar
        snippet += "size=%s\n" % self.getProperty(self.snippetType + 'MarSize')
        # buildid
        snippet += "build=%s\n" % self.getProperty('buildid') # double check case
        # app version
        snippet += "appv=%s\n" % self.getProperty('appVersion')
        # extension version (same as app version)
        snippet += "extv=%s\n" % self.getProperty('appVersion')
        return StringIO(snippet)

    def start(self):
        version = self.slaveVersion("downloadFile")
        if not version:
            m = "slave is too old, does not know about downloadFile"
            raise BuildSlaveTooOldError(m)

        self.step_status.setText(['creating', 'snippets'])

        self.stdio_log = self.addLog("stdio")
        self.stdio_log.addStdout("Starting snippet generation\n")

        d = self.makeSnippet()
        d.addCallback(self.finished).addErrback(self.failed)

    def makeSnippet(self):
        fp = self.generateSnippet()
        fileReader = _FileReader(fp)

        self.snippetFilename = self.snippetType + '.update.snippet'

        args = {
            'slavedest': self.snippetFilename,
            'maxsize': self.maxsize,
            'reader': fileReader,
            'blocksize': self.blocksize,
            'workdir': self.updateDir,
            'mode': self.mode
        }

        msg = "Generating %s update in: %s/%s\n" % (self.snippetType,
                                                    self.updateDir,
                                                    self.snippetFilename)
        self.stdio_log.addStdout(msg)

        self.cmd = StatusRemoteCommand('downloadFile', args)
        d = self.runCommand(self.cmd)
        return d.addErrback(self.failed)

    def finished(self, result):
        self.step_status.setText(['create', self.snippetType, 'snippet'])
        if self.cmd and self.cmd.stderr != '':
            self.addCompleteLog('stderr', self.cmd.stderr)

        if self.stdio_log:
            self.stdio_log.addStdout("Snippet generation complete\n\n")

        if self.cmd:
            if self.cmd.rc is None or self.cmd.rc == 0:
                # Other BuildSteps will probably want this data.
                self.setProperty(self.snippetType + 'snippetFilename',
                  path.join(self.updateDir, self.snippetFilename))

                return BuildStep.finished(self, SUCCESS)
        return BuildStep.finished(self, result)


class CreateCompleteUpdateSnippet(CreateUpdateSnippet):
    def __init__(self, **kwargs):
        CreateUpdateSnippet.__init__(self, snippetType='complete', **kwargs)
        

class CreatePartialUpdateSnippet(CreateUpdateSnippet):
    def __init__(self, **kwargs):
        CreateUpdateSnippet.__init__(self, snippetType='partial', **kwargs)
