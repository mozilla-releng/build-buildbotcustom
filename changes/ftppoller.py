import time
import re

from urllib2 import urlopen, unquote
from twisted.python import log, failure
from twisted.internet import defer, reactor
from twisted.internet.task import LoopingCall

from buildbot.changes import base, changes

class InvalidResultError(Exception):
    def __init__(self, value="InvalidResultError"):
        self.value = value
    def __str__(self):
        return repr(self.value)

class EmptyResult(Exception):
    pass

class FtpPoller(base.ChangeSource):
    """This source will poll an ftp directory searching for a specific file and when found
    trigger a change to the change master."""
    
    compare_attrs = ["ftpURLs", "pollInterval", "branch"]
    
    parent = None # filled in when we're added
    loop = None
    volatile = ['loop']
    working = 0
    gotFile = 1
    
    def __init__(self, branch="", pollInterval=30, ftpURLs=[], searchString=""):
        """
        @type   ftpURLs:            list of strings
        @param  ftpURLs:            The ftp directories to monitor

        @type   branch:             string
        @param  branch:             The branch to look for changes in. This must
                                    match the 'branch' option for the Scheduler.
        @type   pollInterval:       int
        @param  pollInterval:       The time (in seconds) between queries for 
                                    changes
        @type   searchString:       string
        @param  searchString:       name of the file we are looking for
        """
        
        self.ftpURLs = ftpURLs
        self.branch = branch
        self.pollInterval = pollInterval
        self.searchString = searchString
    
    def startService(self):
        self.loop = LoopingCall(self.poll)
        base.ChangeSource.startService(self)
        
        reactor.callLater(0, self.loop.start, self.pollInterval)
    
    def stopService(self):
        self.loop.stop()
        return base.ChangeSource.stopService(self)
    
    def describe(self):
        str = ""
        str += "<br>Using branch %s" % (self.branch)
        return str
    
    def poll(self):
        if self.working > 0:
            log.msg("Not polling Tinderbox because last poll is still working (%s)" % (str(self.working)))
        else:
            for url in self.ftpURLs:
              self.working = self.working + 1
              d = self._get_changes(url)
              d.addCallback(self._process_changes, 0)
              d.addBoth(self._finished)
        return

    def _finished(self, res):
        self.working = self.working - 1

    def _get_changes(self, url):
        log.msg("Polling dir %s" % url)
        return defer.maybeDeferred(urlopen, url)
    
    def _process_changes(self, query, forceDate):
    
        try:
          counter = 0
          url = query.geturl()
          pageContents = query.read()
          query.close()
          lines = pageContents.split('\n')
          """ Check through lines to see if file exists """
          # scenario 1:
          # buildbot restarts or file already exists, so we don't want to trigger anything
          if self.gotFile == 1:
            for line in lines:
              if self.searchString in line:
                continue
              else:
                counter += 1
            # if the file does not exist, then set gotFile to 0 
            if counter == len(lines):
              self.gotFile = 0;
          # scenario 2: 
          # gotFile is 0, we are waiting for a match to trigger build
          if self.gotFile == 0:
            for line in lines:
              # if we have a new file, trigger a build
              if self.searchString in line:
                self.gotFile = 1
                log.msg("Triggering a build, found %s" % self.searchString)
                c = changes.Change(who = url,
                               comments = "success",
                               files = [],
                               branch = self.branch)
                self.parent.addChange(c)
                continue
        except InvalidResultError, e:
            log.msg("Could not process Tinderbox query: " + e.value)
            return
        except EmptyResult:
            return

