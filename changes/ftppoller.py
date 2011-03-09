import re

from twisted.python import log
from twisted.internet import reactor
from twisted.internet.task import LoopingCall
from twisted.web.client import getPage

from buildbot.changes import base, changes
from buildbotcustom.l10n import ParseLocalesFile

class FtpPollerBase(base.ChangeSource):
    """This source will poll an ftp directory searching for a specific file and when found
    trigger a change to the change master."""

    compare_attrs = ["ftpURLs", "pollInterval", "branch"]

    parent = None # filled in when we're added
    loop = None
    volatile = ['loop']
    working = 0

    def __init__(self, branch="", pollInterval=30, ftpURLs=None, timeout=30):
        """
        @type   ftpURLs:            list of strings
        @param  ftpURLs:            The ftp directories to monitor

        @type   branch:             string
        @param  branch:             The branch to look for changes in. This must
                                    match the 'branch' option for the Scheduler.
        @type   pollInterval:       int
        @param  pollInterval:       The time (in seconds) between queries for
                                    changes
        """

        self.ftpURLs = ftpURLs or []
        self.branch = branch
        self.pollInterval = pollInterval
        self.timeout = timeout

    def startService(self):
        self.loop = LoopingCall(self.poll)
        base.ChangeSource.startService(self)

        reactor.callLater(0, self.loop.start, self.pollInterval)

    def stopService(self):
        if self.loop.running:
            self.loop.stop()
        return base.ChangeSource.stopService(self)

    def describe(self):
        desc = ""
        desc += "<br>Using branch %s" % (self.branch)
        return desc

    def poll(self):
        if self.working > 0:
            log.msg("Not polling FtpPollerBase because last poll is still working (%s)" % (str(self.working)))
        else:
            for url in self.ftpURLs:
                self.working = self.working + 1
                d = self._get_changes(url)
                d.addCallback(self._process_changes, url)
                d.addBoth(self._finished)
        return

    def _finished(self, _):
        self.working = self.working - 1

    def _get_changes(self, url):
        return getPage(url, timeout=self.timeout)

    def _process_changes(self, pageContents, url):
        if self.parseContents(pageContents):
            c = changes.Change(who = url,
                           comments = "success",
                           files = [],
                           branch = self.branch)
            self.parent.addChange(c)


class FtpPoller(FtpPollerBase):
    compare_attrs = FtpPollerBase.compare_attrs + ['searchString']
    gotFile = 1

    def __init__(self, searchString="", **kwargs):
        """
        @type   searchString:       string or regex instance
        @param  searchString:       string to search for in retrieve page OR
                                    regex to match page against (uses search())
        """
        if isinstance(searchString, basestring):
            self.searchString = re.compile(re.escape(searchString))
        else:
            self.searchString = searchString
        FtpPollerBase.__init__(self, **kwargs)

    def parseContents(self, pageContents):
        """ Check through lines to see if file exists """
        # scenario 1:
        # buildbot restarts or file already exists, so we don't want to trigger anything
        if self.gotFile == 1:
            if re.search(self.searchString, pageContents):
                return False
            else:
                self.gotFile = 0
        # scenario 2:
        # gotFile is 0, we are waiting for a match to trigger build
        if self.gotFile == 0:
            if re.search(self.searchString, pageContents):
                self.gotFile = 1
                return True


class LocalesFtpPoller(FtpPollerBase):
    """
    Poll a given ftp URL for a list of strings (rather than a single string
    as in FtpPoller) and fire a sendchange to the given branch when ALL of
    the strings are present
    """
    compare_attrs = FtpPollerBase.compare_attrs + ['localesFile', 'platform',
                                                   'sl_platform_map']
    gotAllFiles = True
    localesFile = None
    platform = None
    sl_platform_map = None

    def __init__(self, localesFile, platform, sl_platform_map, **kwargs):
        """
        @type   localesFile:      string URL
        @param  localesFile:      location of the shipped-locales file
                                  containing list of locales and platforms
        @type   platform:         string in list of supported platforms
        @param  platform:         platform to limit locale search

        @type   sl_platform_map:  dict of platforms -> string
        @param  sl_platform_map:  platforms -> platform strings in localesFile
        """
        self.localesFile = localesFile
        self.platform = platform
        self.sl_platform_map = sl_platform_map
        FtpPollerBase.__init__(self, **kwargs)

    def _get_page(self, url):
        return getPage(url, timeout=self.timeout)

    def _get_ftp(self, locales, url):
        """Poll the ftp page with the given url. Return the page as a string
           along with the list of locales from the previous callback"""
        d = self._get_page(url)
        d.addCallback(lambda result: {'pageContents': result, 'locales': locales})
        return d

    def poll(self):
        """Set up a deferred chain to grab the shipped-locales file,
           then use the list of locales to check against the locales
           available on the ftp page"""
        if self.working > 0:
            log.msg("Not polling LocalesFtpPoller because last poll is still working (%s)" % (str(self.working)))
        else:
            self.working = self.working + 1
            d = self._get_page(self.localesFile)
            d.addCallback(self._get_locales)
            for url in self.ftpURLs:
                d.addCallback(self._get_ftp, url)
                d.addCallback(self._process_changes, url)
            d.addBoth(self._finished)

    def _get_locales(self, pageContents):
        """parse the locales file and filter by platform"""
        parsedLocales = ParseLocalesFile(pageContents)
        return [re.compile(re.escape("%s/" % l)) for l in parsedLocales if len(parsedLocales[l]) == 0 or self.sl_platform_map[self.platform] in parsedLocales[l]]


    def searchAllStrings(self, pageContents, locales):
        """match the ftp page against the locales list"""
        req_matches = len(locales)
        #count number of strings with at least one positive match
        matches = sum([1 for regex in locales if re.search(regex, pageContents)])
        return matches == req_matches

    def parseContents(self, pageContents, locales):
        """ Check through lines to see if file exists """
        # scenario 1:
        # buildbot restarts or all files already exist, so we don't want to trigger anything
        if self.gotAllFiles:
            if self.searchAllStrings(pageContents, locales):
                return False
            else:
                self.gotAllFiles = False
        # scenario 2:
        # gotAllFiles is False, we are waiting for a match to trigger build
        else:
            if self.searchAllStrings(pageContents, locales):
                self.gotAllFiles = True
                return True

    def _process_changes(self, results, url):
        """ Take the results of polling the locales list and ftp page
            and send a change to the parent branch if all the locales
            are ready """
        pageContents = results['pageContents']
        locales = results['locales']
        if self.parseContents(pageContents, locales):
            c = changes.Change(who = url,
                           comments = "success",
                           files = [],
                           branch = self.branch)
            self.parent.addChange(c)
        #return the locales list for the next ftp poller in the callback chain
        return locales

class UrlPoller(FtpPollerBase):
    compare_attrs = FtpPollerBase.compare_attrs + ['url']
    gotFile = True

    def __init__(self, url, **kwargs):
        self.url = url
        FtpPollerBase.__init__(self, **kwargs)

    def poll(self):
        if self.working > 0:
            log.msg("Not polling UrlPoller because last poll is still working (%s)" \
                    % self.working)
        else:
            self.working = self.working + 1
            d = self._get_changes(self.url)
            d.addCallback(self._process_changes, self.url)
            d.addErrback(self._process_errback)
            d.addBoth(self._finished)
        return

    def _process_errback(self, _):
        # First 404/500/etc, reset the state to False
        self.gotFile = False

    def parseContents(self, _):
        # First 200, stop polling
        log.msg("Stopping UrlPoller for %s" % self.url)
        self.stopService()

        if not self.gotFile:
            return True
        else:
            # Got the file after buildbot restart
            return False
