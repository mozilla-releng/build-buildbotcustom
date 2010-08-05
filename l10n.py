# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2007
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Axel Hecht <l10n@mozilla.com>
#   Armen Zambrano Gasparnian <armenzg@mozilla.com>
#   Chris AtLee <catlee@mozilla.com>
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****

from twisted.python import log
from twisted.internet import defer
from twisted.web.client import getPage
from buildbot.scheduler import Dependent, Triggerable, Nightly
from buildbot.sourcestamp import SourceStamp
from buildbot.process import properties
from buildbot.status.builder import SUCCESS, WARNINGS

def ParseLocalesFile(data):
    """
    @type  data: string
    @param data: The contents of all-locales or shipped-locales files

    This function creates a dictionary that has locales as the keys
    and the value associated can be a list of platforms for which the
    locale should be repackaged on (think of ja and ja-JP-mac)
    """
    locales = {}
    data = data.strip()
    for line in data.split('\n'):
        splitLine = line.split()
        locale = splitLine[0]
        buildPlatforms = splitLine[1:]
        if locale in locales:
            for plat in buildPlatforms:
                if plat not in locales[locale]:
                    locales[locale].append(plat)
        else:
            locales[locale] = buildPlatforms
    return locales

class L10nMixin(object):
    """
    This class helps any of the L10n custom made schedulers
    to submit BuildSets as specified per list of locales, or either a
    'all-locales' or 'shipped-locales' file via a call to createL10nBuilds.

    For each locale, there will be a build property 'locale' set to the
    inidividual locale to be built for that BuildSet.
    """

    def __init__(self, platform, repo='http://hg.mozilla.org/', branch=None,
            baseTag='default', localesFile="browser/locales/all-locales",
            locales=None):
        self.branch = branch
        self.baseTag = baseTag
        self.repo = repo
        self.localesFile = localesFile

        # if the user wants to use something different than all locales
        # check ParseLocalesFile function to note that we now need a dictionary
        # with the locale as the key and a list of platform as the value for
        # each key to build a specific locale e.g. locales={'fr':['osx']}
        self.locales = locales
        # Make sure a supported platform is passed. Allow variations, but make
        # sure to convert them to the form the locales files ues.
        assert platform in ('linux', 'linux64', 'win32', 'macosx', 'macosx64',
                'osx', 'osx64', 'maemo', 'wince', 'android-r7')

        self.platform = platform
        if self.platform.startswith('macosx'):
            self.platform = 'osx'
        if self.platform.startswith('linux'):
            self.platform = 'linux'

    def _cbLoadedLocales(self, t, locales, reason, set_props):
        """
        This is the callback function that gets called once the list
        of locales are ready to be processed
        Let's fill the queues per builder and submit the BuildSets per each locale
        """
        log.msg("L10nMixin:: loaded locales' list")
        db = self.parent.db
        for locale in locales:
            # Ignore en-US. It appears in locales files but we do not repack it.
            if locale == "en-US":
                continue
            # Some locales should only be built on certain platforms, make sure to
            # obey those rules.
            if len(locales[locale]) > 0:
                if self.platform not in locales[locale]:
                    continue
            props = properties.Properties()
            props.updateFromProperties(self.properties)
            if set_props:
                props.updateFromProperties(set_props)
            #I do not know exactly what to pass as the source parameter
            props.update(dict(locale=locale), "Scheduler")
            props.setProperty("en_revision", self.baseTag, "L10nMixin")
            props.setProperty("l10n_revision", self.baseTag, "L10nMixin")
            log.msg('Submitted '+locale+' locale')
            # let's submit the BuildSet for this locale
            # Create a sourcestamp
            ss = SourceStamp(branch=self.branch)
            ssid = db.get_sourcestampid(ss, t)
            self.create_buildset(ssid, reason, t, props=props)

    def getLocales(self, revision=None):
        """
        It returns a list of locales if the user has set a list of locales
        in the scheduler OR it returns a Deferred.

        You want to call this method via defer.maybeDeferred().
        """
        if self.locales:
            log.msg('L10nMixin.getLocales():: The user has set a list of locales')
            return self.locales
        else:
            revision = revision or self.baseTag
            localesURL = "%s%s/raw-file/%s/%s" \
                                            % (self.repo, self.branch, revision, self.localesFile)
            log.msg("L10nMixin:: Getting locales from: "+localesURL)
            # we expect that getPage will return the output of "all-locales"
            # or "shipped-locales" or any file that contains a locale per line
            # in the begining of the line e.g. "en-GB" or "ja linux win32"
            # getPage returns a defered that will return a string
            d = getPage(localesURL, timeout = 5 * 60)
            d.addCallback(lambda data: ParseLocalesFile(data))
            return d

    def createL10nBuilds(self, revision=None, reason=None, set_props=None):
        """
        We request to get the locales that we have to process and which
        method to call once they are ready
        """
        log.msg('L10nMixin:: A list of locales is going to be requested')
        d = defer.maybeDeferred(self.getLocales, revision)
        d.addCallback(lambda locales: self.parent.db.runInteraction(
            self._cbLoadedLocales, locales, reason, set_props))
        return d

class TriggerableL10n(Triggerable, L10nMixin):
    """
    TriggerableL10n is used to paralellize the generation of l10n builds.

    TriggerableL10n is designed to be used with a Build factory that gets the
    locale to build from the 'locale' build property.
    """

    compare_attrs = ('name', 'builderNames', 'branch')

    def __init__(self, name,  builderNames, platform,
            repo='http://hg.mozilla.org/', branch=None, baseTag='default',
            localesFile=None, locales=None):
        L10nMixin.__init__(self, platform=platform, repo=repo, branch=branch,
                baseTag=baseTag, localesFile=localesFile, locales=locales)
        Triggerable.__init__(self, name, builderNames)

    def trigger(self, ss, set_props=None):
        reason = "This build was triggered by the successful completion of the en-US nightly."
        self.createL10nBuilds(revision=ss.revision, reason=reason, set_props=set_props)


class DependentL10n(Dependent, L10nMixin):
    """
    This scheduler runs some set of 'downstream' builds when the
    'upstream' scheduler has completed successfully.
    """

    compare_attrs = ('name', 'upstream', 'builders')

    def __init__(self, name, upstream, builderNames, platform,
            repo='http://hg.mozilla.org/', branch=None, baseTag='default',
            localesFile=None, locales=None):
        Dependent.__init__(self, name, upstream, builderNames)
        L10nMixin.__init__(self, platform=platform, branch=branch,
                baseTag=baseTag, localesFile=localesFile,
                locales=locales)

    def run(self):
        d = self.parent.db.runInteraction(self._run)

        def _createBuilds(sses):
            # This gets called with a list of SourceStamps to do builds for
            l = []
            for ss in sses:
                d1 = self.createL10nBuilds(ss.revision)
                l.append(d1)
            return defer.gatherResults(l)

        d.addCallback(_createBuilds)
        # Return None here since we don't need to be woken up in a specific
        # amount of time.
        d.addCallback(lambda ign: None)

        return d

    def _run(self, t):
        db = self.parent.db
        res = db.scheduler_get_subscribed_buildsets(self.schedulerid, t)
        # this returns bsid,ssid,results for all of our active subscriptions.
        # We ignore the ones that aren't complete yet. This leaves the
        # subscription in place until the buildset is complete.
        sses = []
        for (bsid,ssid,complete,results) in res:
            if complete:
                if results in (SUCCESS, WARNINGS):
                    ss = db.getSourceStampNumberedNow(ssid, t)
                    sses.append(ss)
                db.scheduler_unsubscribe_buildset(self.schedulerid, bsid, t)
        return sses

class MultiNightlyL10n(Nightly, L10nMixin):
    '''This scheduler creates a property which is a list of locales.
    The factories that want to use this should implement "newBuild(self, request)"
    and grab the last request which contains the property "locales"
    '''
    def __init__(self, platform, branch=None, localesFile=None, **kwargs):
        L10nMixin.__init__(self, platform=platform, branch=branch,
                localesFile=localesFile)
        Nightly.__init__(self, **kwargs)

    def _cbLoadedLocales(self, t, locales, reason, set_props):
        """
        Instead of submitting a job per locale, we just append a list of locales
        to a BuildSet's property and let the factory use it through the BuildRequests
        """
        locales = sorted(locales.keys())

        props = properties.Properties()
        props.updateFromProperties(self.properties)
        props.setProperty('locales', locales, "MultiNightlyL10n")

        ss = SourceStamp(branch=self.branch)
        ssid = db.get_sourcestampid(ss, t)
        self.create_buildset(ssid, reason, t, props=props)

    def start_HEAD_build(self, t):
        # This gets called by Nightly when it's time to kick off a new build
        return self.createL10nBuilds()

