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

from twisted.python import log as log2
from buildbotcustom import log
from buildbot.scheduler import BaseUpstreamScheduler, Dependent, Triggerable 
from buildbot.sourcestamp import SourceStamp
from buildbot import buildset, process
from buildbot.changes import changes
from buildbot.process import properties
from twisted.internet import protocol, utils, reactor, error, defer
from twisted.web.client import HTTPClientFactory, getPage
from itertools import izip
from ConfigParser import ConfigParser, NoSectionError, NoOptionError
from collections import defaultdict
from cStringIO import StringIO
import os
import os.path
import re
import shlex
from urlparse import urljoin, urlparse
from urllib import pathname2url, url2pathname


# NOTE: The following class has been copied from compare-locales
# keep in sync and remove once compare-locales is on the master
class L10nConfigParser(object):
  '''Helper class to gather application information from ini files.

  This class is working on synchronous open to read files or web data.
  Subclass this and overwrite loadConfigs and addChild if you need async.
  '''
  def __init__(self, inipath, **kwargs):
    """Constructor for L10nConfigParsers
    
    inipath -- l10n.ini path
    Optional keyword arguments are fowarded to the inner ConfigParser as
    defaults.
    """
    if os.path.isabs(inipath):
      self.inipath = 'file:%s' % pathname2url(inipath)
    else:
      pwdurl = 'file:%s/' % pathname2url(os.getcwd())
      self.inipath = urljoin(pwdurl, inipath)
    # l10n.ini files can import other l10n.ini files, store the 
    # corresponding L10nConfigParsers
    self.children = []
    # we really only care about the l10n directories described in l10n.ini
    self.dirs = []
    # optional defaults to be passed to the inner ConfigParser (unused?)
    self.defaults = kwargs

  def loadConfigs(self):
    """Entry point to load the l10n.ini file this Parser refers to.

    This implementation uses synchronous loads, subclasses might overload
    this behaviour. If you do, make sure to pass a file-like object
    to onLoadConfig.
    """
    self.onLoadConfig(urlopen(self.inipath))

  def onLoadConfig(self, inifile):
    """Parse a file-like object for the loaded l10n.ini file."""
    cp = ConfigParser(self.defaults)
    cp.readfp(inifile)
    try:
      depth = cp.get('general', 'depth')
    except:
      depth = '.'
    self.baseurl = urljoin(self.inipath, depth)
    # create child loaders for any other l10n.ini files to be included
    try:
      for title, path in cp.items('includes'):
        # skip default items
        if title in self.defaults:
          continue
        # add child config parser
        self.addChild(title, path, cp)
    except NoSectionError:
      pass
    # try to load the "dirs" defined in the "compare" section
    try:
      self.dirs.extend(cp.get('compare', 'dirs').split())
    except (NoOptionError, NoSectionError):
      pass
    # try to set "all_path" and "all_url"
    try:
      self.all_path = cp.get('general', 'all')
      self.all_url = urljoin(self.inipath, 'all-locales')
    except (NoOptionError, NoSectionError):
      self.all_path = None
      self.all_url = None

  def addChild(self, title, path, orig_cp):
    """Create a child L10nConfigParser and load it.
    
    title -- indicates the module's name
    path -- indicates the path to the module's l10n.ini file
    orig_cp -- the configuration parser of this l10n.ini
    """
    cp = L10nConfigParser(urljoin(self.baseurl, path), **self.defaults)
    cp.loadConfigs()
    self.children.append(cp)

  def dirsIter(self):
    """Iterate over all dirs and our base path for this l10n.ini"""
    url = urlparse(self.baseurl)
    basepath = None
    if url[0] == 'file':
      basepath = url2pathname(url[2])
    for dir in self.dirs:
      yield (dir, basepath)

  def directories(self):
    """Iterate over all dirs and base paths for this l10n.ini as well
    as the included ones.
    """
    for t in self.dirsIter():
      yield t
    for child in self.children:
      for t in child.directories():
        yield t

  def allLocales(self):
    """Return a list of all the locales of this project"""
    return urlopen(self.all_url).read().splitlines()

class AsyncLoader(L10nConfigParser):
  """AsyncLoader extends L10nConfigParser to do IO with asynchronous twisted
  loads.
  """
  timeout = 30

  def __init__(self, inipath, branch, _type='hg'):
    L10nConfigParser.__init__(self, inipath)
    self.branch = branch
    self.pendingLoads = 0
    self.type = _type

  def loadConfigs(self):
    """Load the l10n.ini file asynchronously.

    Parses the contents in the callback, and fires off further
    loads as needed for included l10n.inis for other modules.
    """
    self.d = defer.Deferred()
    self.pendingLoads += 1
    d = self._getPage(self.inipath)
    def _cbDone(contents):
      """Callback passing the contents of the l10n.ini file to onLoadConfig
      """
      self.onLoadConfig(StringIO(contents))
      return defer.succeed(True)
    def _cbErr(rv):
      """Errback when loading of l10n.ini fails.

      Decrements the pending loads and forwards the error.
      """
      self.pendingLoads -= 1
      return self.d.errback(rv)
    d.addCallback(_cbDone)
    d.addCallbacks(self._loadDone, _cbErr)
    return self.d

  def onLoadConfig(self, f):
    """Overloaded method of L10nConfigParser

    Just adds debug information."""
    log2.msg("onLoadConfig called")
    L10nConfigParser.onLoadConfig(self,f)

  def addChild(self, title, path, orig_cp):
    """Create a child L10nConfigParser and load it.
    
    title -- indicates the module's name
    path -- indicates the path to the module's l10n.ini file
    orig_cp -- the configuration parser this l10n.ini

    Extends L10nConfigParser.addChild to keep track of all
    asynchronous loads
    """

    # check if there's a section with details for this include
    # we might have to check a different repo, or even VCS
    # for example, projects like "mail" indicate in
    # an "include_" section where to find the l10n.ini for "toolkit"
    details = 'include_' + title
    if orig_cp.has_section(details):
      type = orig_cp.get(details, 'type')
      if type not in ['hg', 'cvs']:
        log2.msg("Cannot load l10n.ini for %s with type %s" % (title, type))
        return
      branch = orig_cp.get(details, 'mozilla')
      # Create an asynchronous loader depending if "hg" or "cvs" module
      if type == 'cvs':
        cp = CVSAsyncLoader(orig_cp.get(details, 'l10n.ini'), branch)
      else:
        l10n_ini_temp = '%(repo)s%(mozilla)s/raw-file/default/%(l10n.ini)s'
        cp = AsyncLoader(l10n_ini_temp % dict(orig_cp.items(details)), branch,
                         _type=type)
    else:
      # instantiates the same class as the current one
      # it can be AsyncLoader or CVSAsyncLoader
      cp = self.__class__(urljoin(self.baseurl, path), self.branch,
                          **self.defaults)
    d = cp.loadConfigs()
    self.pendingLoads += 1
    d.addCallbacks(self._loadDone, self.d.errback)
    self.children.append(cp)

  def getAllLocales(self, revision=None):
    all_url = self.all_url
    if revision is not None:
      all_url = all_url.replace('/default/', '/%s/' % revision)
    return self._getPage(all_url)

  def _getPage(self, path):
    return getPage(path, timeout = self.timeout)

  def _loadDone(self, result):
    self.pendingLoads -= 1
    if not self.pendingLoads:
      self.d.callback(True)

class CVSProtocol(protocol.ProcessProtocol):

  def __init__(self, cmd):
    self.d = defer.Deferred()
    self.data = ''
    self.errdata = ''
    self.cmd = cmd

  def connectionMade(self):
    self.transport.closeStdin()

  def outReceived(self, data):
    self.data += data

  def errReceived(self, data):
    # chew away what cvs blurbs at us
    self.errdata += data
    pass

  def processEnded(self, reason):
    if isinstance(reason.value, error.ProcessDone):
      self.d.callback(self.data)
    else:
      reason.value.args = (self.errdata,)
      self.d.errback(reason)

class CVSAsyncLoader(AsyncLoader):
  """CVSAsyncLoader subclasses AsyncLoader to get data via
  cvs co -p
  """
  CVSROOT = ':pserver:anonymous@cvs-mirror.mozilla.org:/cvsroot'

  def __init__(self, inipath, branch, **kwargs):
    AsyncLoader.__init__(self, inipath, branch, **kwargs)
    self.inipath = inipath
    self.type = 'cvs'

  def _getPage(self, path):
    args = ['cvs', '-d', self.CVSROOT, 'co']
    if self.branch is not None:
      args += ['-r', self.branch]
    args += ['-p', path]
    pp = CVSProtocol(' '.join(args))
    reactor.spawnProcess(pp, 'cvs', args, {})
    return pp.d


class repositories(object):
  """
  Helper to store some information about the cvs repositories we use.
  
  It provides the cvsroot and the bonsai url. For l10n purposes, there
  are two functions, expand, and expandLists, which take the locale
  and the module, or lists for both, resp., and returns the list of
  directories, suitable both for cvs check out and bonsai.
  
  Predefined are the repositories mozilla and l10n.
  """
  class _repe:
    def __init__(self, root, base, bonsai):
      self.cvsroot = root
      self.base = base
      self.bonsai = bonsai
      self.expand = lambda loc, mod: 'mozilla/%s/locales/'%mod
      self.repository = 'l10nbld@cvs.mozilla.org:/cvsroot'
    def expandLists(self, locs, list):
      return [self.expand(l, m) for m in list for l in locs]
  mozilla = _repe('/cvsroot', 'mozilla', 'http://bonsai.mozilla.org/')
  l10n = _repe('/l10n', 'l10n', 'http://bonsai-l10n.mozilla.org/')
  l10n.expand = lambda loc, mod: 'l10n/%s/%s/'%(loc,mod)
  l10n.repository = 'l10nbld@cvs.mozilla.org:/l10n'


def configureDispatcher(config, section, scheduler, buildOnEnUS=False):
  """
  Add the Dispatchers for the given section of l10nbuilds.ini to the scheduler.
  
  section -- the name of the section inside of l10nbuilds.ini, aka the tree
  config -- the config parser that loaded l10nbuilds.ini
  scheduler -- scheduler to which we are adding dispatchers
  buildOnEnUS -- optional argument to disable building on en-US landings
  
  Types of dispatchers:
  - AllLocalesWatcher:
      every time all-locales for the app changes, the list of locales is updated
  - EnDispatcher:
      checks for en-US changes and triggers compareOnly builds for all locales
      more than one can be given for a given tree to check multiple repos
  - [Hg]L10nDispatcher:
      checks for changes in a locale and triggers a full build for that locale
  """

  log2.msg('configureDispatcher for ' + section)
  buildtype = config.get(section, 'type')
  if buildtype not in ['cvs', 'hg', 'single-module-hg']:
    raise RuntimeError('type needs to be either cvs, hg, or single-module-hg')
  en_branch = config.get(section, 'mozilla')
  l10n_branch = config.get(section, 'l10n')
  props = {'en_branch': en_branch,
           'l10n_branch': l10n_branch}
  if config.has_option(section, 'en_us_binary'):
    props['en_us_binary'] = config.get(section, 'en_us_binary')
  if buildtype == 'cvs':
    CVSAsyncLoader.CVSROOT = repositories.mozilla.repository
    cp = CVSAsyncLoader(config.get(section, 'l10n.ini'), en_branch)
  else:
    l10n_ini_temp = '%(repo)s%(mozilla)s/raw-file/default/%(l10n.ini)s'
    # substitute "repo", "mozilla" and "l10n.ini" from the l10nbuilds.ini file 
    cp = AsyncLoader(l10n_ini_temp % dict(config.items(section)), en_branch,
                     _type = buildtype)

  # Declaring few functions that are going to be used as callback functions
  # immediatelly after being declared
  # 1) _addDispatchers(dirs, builders)
  # 2) _cbLoadedConfig(rv)
  # 3) _errBack(msg) 
  def _addDispatchers(dirs, builders):
    """
    This function is called at the end of _cbLoadedConfig's execution
    Let's add the EnDispatcher(s) and the (Hg)L10nDispatcher for this project/branch
    """
    alldirs = []
    if dirs['cvs']:
      # Let's add an EnDispatcher to watch for en-US entities' changes
      # We can have more than one EnDispatcher
      for branch, endirs in dirs['cvs'].iteritems():
        scheduler.addDispatcher(EnDispatcher(endirs, branch, section,
                                             prefix = 'mozilla/'))
        alldirs += endirs
      log2.msg('en cvs dispatchers added for ' + section)
    if dirs['hg'] or dirs['single-module-hg']:
      revisions = ['en', 'l10n']
      if config.has_option(section, 'revisions'):
        # we're given a list of foo_revision properties to set to 'default'
        revisions = map(None, config.get(section, 'revisions').split())
      props.update(dict.fromkeys((s+'_revision' for s in revisions), 'default'))
      for branch, endirs in dirs['hg'].iteritems():
        if buildOnEnUS:
          log2.msg('adding EnDispatcher for %s on %s for %s' %
                   (section, branch, ' '.join(endirs)))
          scheduler.addDispatcher(EnDispatcher(endirs, branch, section))
        alldirs += endirs
      for branch, endirs in dirs['single-module-hg'].iteritems():
        log2.msg('adding EnDispatcher for single module %s on %s for %s' %
                 (section, branch, ' '.join(endirs)))
        if len(endirs) > 1:
          log2.msg('WARNING: More than one dir for a single module?')
        # the EnDispatcher for a single module just listens to
        # 'locales/en-US/foo', i.e., has only a single empty dir
        if buildOnEnUS:
          scheduler.addDispatcher(EnDispatcher([''], branch, section))
        alldirs += endirs
        

      # if we have one hg dispatcher, l10n is on hg
      scheduler.addDispatcher(HgL10nDispatcher(alldirs, l10n_branch,
                                               section))
      log2.msg('both hg dispatchers added for ' + section)
    else:
      # only pure cvs projects have l10n on cvs
      scheduler.addDispatcher(L10nDispatcher(alldirs, l10n_branch,
                                             section))
      log2.msg('l10n cvs dispatchers added for ' + section)

    scheduler.treeprops[section].update(props)

    # This adds in the builder's column a "%(section)s set up" message to
    # indicate that all loading has been complete for that section
    buildermap = scheduler.parent.botmaster.builders
    for b in builders:
      try:
        buildermap[b].builder_status.addPointEvent([section, "set", "up"])
      except KeyError:
        log2.msg("Can't find builder %s for %s" % (b, section))
  def _cbLoadedConfig(rv):
    # All the l10n.ini required files have been loaded asynchronously and
    # we are ready to add the required dispatchers
    log2.msg('config loaded for ' + section)
    dirs = {'hg':{}, 'cvs':{}, 'single-module-hg':{}}
    # Let's iterate through the ConfigParser (aka loader)
    # to get all the directories
    # l.type indicates if the module lives in "cvs" or "hg"
    loaders = [cp]
    while loaders:
      l = loaders.pop(0)
      ldirs = dict(l.dirsIter()).keys()
      if l.branch not in dirs[l.type]:
        dirs[l.type][l.branch] = ldirs
      else:
        dirs[l.type][l.branch] += ldirs
      # Let's append at the end any children (which are loaders)
      # that the current loader has
      loaders += l.children
    # Let's sort the keys for debugging purposes
    for d in dirs.itervalues():
      for dd in d.itervalues():
        dd.sort()

    builders = shlex.split(config.get(section, 'builders'))
    scheduler.builders[section] = builders
    scheduler.apps[section] = config.get(section, 'app')

    locales = config.get(section, 'locales')
    if locales == 'all':
      # add AllLocalesWatcher, cvs and hg have different paths, i.e.,
      # cvs has a leading 'mozilla/'
      if cp.type == 'cvs':
        path = cp.all_url
      else:
        # e.g. browser/locales/all-locales
        path = cp.all_path
      scheduler.addDispatcher(AllLocalesWatcher(en_branch,
                                                path,
                                                cp,
                                                section))
    else:
      # Just use the given list of locales, picks up changes on reconfig
      scheduler.locales[section] = locales.split()
    # Let's add the rest of the dispatchers
    _addDispatchers(dirs, builders)
    return
  def _errBack(msg):
    log2.msg("loading %s failed with %s" % (section, msg.value.message))
    log2.msg(section + " has inipath " + cp.inipath)
    buildermap = scheduler.parent.botmaster.builders
    # for b in builders:
    #   buildermap[b].builder_status.addPointEvent([section, "setup", "failed"])
  d = cp.loadConfigs()
  d.addCallbacks(_cbLoadedConfig, _errBack)
  return d

"""
Dispatchers

The dispatchers know about which changes impact which localizations and 
applications. They're used in the Scheduler, and enable the scheduler to 
work on multiple branch combinations and multiple applications.
"""
class IBaseDispatcher(object):
  """
  Interface for dispatchers.
  """
  log = "dispatcher"
  
  def dispatchChange(self, change):
    """
    Scheduler calls dispatch for each change, the dispatcher is expected
    to call sink.queueBuild for each build that is supposed to run.
    Scheduler will coalescent duplicate builders.
    """
    raise NotImplented

  def setParent(self, parent):
    self.parent = parent

  def debug(self, msg):
    log.debug(self.log, msg)

class AllLocalesWatcher(IBaseDispatcher):
  """
  Dispatcher to watch for changes for changes to all-locales files.

  If such a change comes in, it reloads the all-locales file, and 
  updates the scheduler's locales map.
  """

  def __init__(self, branch, all_urls_path, loader, tree):
    self.branch = branch
    self.path = all_urls_path
    self.loader = loader
    self.tree = tree
    self.log += '.'+  tree

  def setParent(self, parent):
    IBaseDispatcher.setParent(self, parent)
    # do the initial loading of all-locales
    d = self.loader.getAllLocales()
    d.addCallbacks(self.callback, self.errback)
    

  def dispatchChange(self, change):
    self.debug("changed %s for %s?" % (self.path, self.tree))
    # Let's verify that the change is for our branch
    if change.branch != self.branch:
      return
    if self.path not in change.files:
      return
    # Let's get the contents of all-locales in the repo
    self.debug("update all-locales for %s" % self.tree)
    kwargs = {}
    if change.revision:
      kwargs['revision'] = change.revision
    d = self.loader.getAllLocales(**kwargs)
    d.addCallbacks(self.callback, self.errback)

  def callback(self, locales):
    """Callback for content of all-locales.

    split() the content and update the scheduler's locales map
    """
    self.debug("all-locales loaded for %s" % self.tree)
    locales = locales.split()
    self.parent.locales[self.tree] = locales

  def errback(self, failure):
    self.debug("loading all locales for %s failed with %s" % (self.tree,
                                                              failure.value.message))


class L10nDispatcher(IBaseDispatcher):
  """
  Dispatcher taking care about one branch in the l10n rep.
  
  It's using 
  - an array of module-app tuples and
  - a hash mapping apps to locales
  to figure out which apps-locale combos need to be built. It ignores
  changes that are not affecting its combos.
  It can pass around a tree name, too.
  """
  
  def __init__(self, paths, l10n_branch, tree,
               props = {}):
    self.paths = paths
    self.l10n_branch = l10n_branch
    self.tree = tree
    self.parent = None
    self.log += '.l10n'
    self.log += '.' + tree
    self.props = props

  def dispatchChange(self, change):
    self.debug("adding change %d" % change.number)
    toBuild = {}
    if self.l10n_branch and self.l10n_branch != change.branch:
      self.debug("not our branch, ignore, %s != %s" %
                 (self.l10n_branch, change.branch))
      return
    if self.tree not in self.parent.locales:
      # we're probably still waiting for all-locales to load, ignore
      # this change and go on
      self.debug("locales list for %s not set up, ignoring change" % self.tree)
      return
    for file in change.files:
      pathparts = file.split('/',2)
      if pathparts[0] != 'l10n':
        self.debug("non-l10n changeset ignored")
        return
      if len(pathparts) != 3:
        self.debug("l10n path doesn't contain locale and path")
        return
      loc, path = pathparts[1:]
      if loc not in self.parent.locales[self.tree]:
        continue
      for basepath in self.paths:
        if not path.startswith(basepath):
          continue
        toBuild[loc] = True
      self.debug("adding %s for %s" % (self.tree, loc))
    for loc in toBuild.iterkeys():
      self.parent.queueBuild(loc, change, self.tree, self.props)

class HgL10nDispatcher(L10nDispatcher):
  def dispatchChange(self, change):   
    self.debug("adding change %d" % change.number)
    if self.l10n_branch and self.l10n_branch != change.branch:
      self.debug("not our branch, ignore, %s != %s" %
                 (self.l10n_branch, change.branch))
      return
    if not hasattr(change, 'locale'):
      log2.msg("I'm confused, the branches match, but this is not a locale change")
      return
    if self.tree not in self.parent.locales:
      # we're probably still waiting for all-locales to load, ignore
      # this change and go on
      self.debug("locales list for %s not set up, ignoring change" % self.tree)
      return
    if change.locale not in self.parent.locales[self.tree]:
      # this is a change to a locale that this build doesn't pay attention to
      return
    # Only changes in the modules mentioned in self.paths should trigger a build
    doBuild = False
    for file in change.files:
      for basepath in self.paths:
        if file.startswith(basepath):
          doBuild = True
          break
    if not doBuild:
      self.debug("dropping change %d, not our app" % change.number)
      self.debug("%s listens to %s" % (self.tree, ' '.join(self.paths)))
      return
    self.parent.queueBuild(change.locale, change, self.tree, self.props)

class EnDispatcher(IBaseDispatcher):
  """
  Dispatcher watching one branch on the main mozilla repository.
  It's using 
  - an array of module-app tuples and
  - a hash mapping apps to locales
  to figure out which apps-locale combos need to be built. For each application
  affected by a change, it queues a build for all locales for that app.
  It can pass around a tree name, too.
  """
  
  def __init__(self, paths, branch, tree, prefix = '',
               props = {}):
    self.paths = paths
    self.branch = branch
    self.tree = tree
    self.parent = None
    self.log += '.en'
    self.log += '.' + tree
    self.prefix = prefix
    self.props = dict(props)
    self.props['compareOnly'] = True
    self.en_pattern = re.compile('(?P<module>.*?)/?locales/en-US/')

  def setParent(self, parent):
    self.parent = parent

  def dispatchChange(self, change):
    self.debug("adding change %d" % change.number)
    if self.branch and self.branch != change.branch:
      self.debug("not our branch, ignore, %s != %s" %
                 (self.branch, change.branch))
      return
    if self.tree not in self.parent.locales:
      self.debug("parent locales not set up, ignoring change")
      return
    needsBuild = False
    for file in change.files:
      if not file.startswith(self.prefix):
        self.debug("Ignoring change %d, not our rep" % change.number)
        return
      file = file.replace(self.prefix, '', 1)
      m = self.en_pattern.match(file)
      if not m:
        continue
      basedir = m.group('module')
      if basedir in self.paths:
        needsBuild = True
        break
    if needsBuild:
      for loc in self.parent.locales[self.tree]:
        self.parent.queueBuild(loc, change, self.tree, self.props)


class Scheduler(BaseUpstreamScheduler):
  """
  Scheduler used for l10n builds.

  It's using several Dispatchers to create build items depending
  on the submitted changes.
  """
  
  compare_attrs = ('name', 'treeStableTimer', 'inipath',
                   'builders', 'apps', 'locales', 'treeprops',
                   'properties')
  
  def __init__(self, name, inipath, treeStableTimer = None, buildOnEnUS=False,
               nomerge=True):
    """
    @param name: the name of this Scheduler
    @param treeStableTimer: the duration, in seconds, for which the tree
                            must remain unchanged before a build will be
                            triggered. This is intended to avoid builds
                            of partially-committed fixes.
    """
    
    BaseUpstreamScheduler.__init__(self, name)
    # path to the l10nbuilds.ini file that is read synchronously
    self.inipath = inipath
    self.treeStableTimer = treeStableTimer
    self.nextBuildTime = None
    self.timer = None
    self.buildOnEnUS = buildOnEnUS

    # will hold the dispatchers for each tree
    self.dispatchers = []
    # list of locales for each tree
    self.locales = {}
    # list of builders for each tree
    self.builders = {}
    # app per tree
    self.apps = {}
    # properties per tree
    self.treeprops = defaultdict(dict)
    # use NoMergeStamp or regular stamp
    self.nomerge = nomerge

  def startService(self):
    log2.msg("starting l10n scheduler")
    cp = ConfigParser()
    cp.read(self.inipath)
    # Configure the dispatchers for our trees as soon as the reactor is running
    for tree in cp.sections():
      reactor.callWhenRunning(configureDispatcher,
                              cp, tree, self, buildOnEnUS=self.buildOnEnUS)

  class NoMergeStamp(SourceStamp):
    """
    We're going to submit a bunch of build requests for each change. That's
    how l10n goes. This source stamp impl keeps them from being merged by
    the build master.
    """
    def canBeMergedWith(self, other):
      return False

  # dispatching routines
  def addDispatcher(self, dispatcher):
    """
    Add an IBaseDispatcher instance to this Scheduler.
    """
    self.dispatchers.append(dispatcher)
    dispatcher.setParent(self)

  def queueBuild(self, locale, change_or_changes, tree, misc_props={}):
    """
    Callback function for dispatchers to tell us what to build.
    This function actually submits the buildsets on non-mergable
    sourcestamps.
    """
    if isinstance(change_or_changes, changes.Change):
      _changes = [change_or_changes]
    else:
      _changes = change_or_changes
    log.debug("scheduler", "queueBuild: build %s for change %d" % 
              (', '.join(self.builders[tree]), _changes[0].number))
    props = properties.Properties()
    props.updateFromProperties(self.properties)
    if tree in self.treeprops:
      props.update(self.treeprops[tree], 'Scheduler')
    props.update(dict(app=self.apps[tree], locale=locale, tree=tree,
                      needsCheckout = True), 'Scheduler')
    if misc_props:
      props.update(misc_props, 'Scheduler')
    if self.nomerge:
      ss = Scheduler.NoMergeStamp(changes=_changes)
    else:
      ss = SourceStamp(changes=_changes)
    bs = buildset.BuildSet(self.builders[tree], ss,
                           reason = "%s %s" % (tree, locale),
                           properties = props)
    self.submitBuildSet(bs)
  
  # Implement IScheduler
  def addChange(self, change):
    log.debug("scheduler",
              "addChange: Change %d, %s" % (change.number, change.asText()))
    for dispatcher in self.dispatchers:
      dispatcher.dispatchChange(change)

  def listBuilderNames(self):
    builders = set()
    for bs in self.builders.itervalues():
      builders.update(bs)
    return list(builders)

  def getPendingBuildTimes(self):
    if self.nextBuildTime is not None:
      return [self.nextBuildTime]
    return []

DEFAULT_CVSROOT = ':pserver:anonymous@cvs-mirror.mozilla.org:/cvsroot'

class L10nMixin(object):
  """
  This class helps any of the L10n custom made schedulers
  to submit BuildSets as specified per list of locales, or either a
  'all-locales' or 'shipped-locales' file via a call to createL10nBuilds.
  
  For each locale, there will be a build property 'locale' set to the
  inidividual locale to be built for that BuildSet.
  """

  def __init__(self,
               platform,
               repo        = 'http://hg.mozilla.org/',
               branch      = None,
               repoType    = 'cvs',
               baseTag     = 'default',
               localesFile = None,
               cvsRoot     = DEFAULT_CVSROOT,
               locales     = None,
               tree        = None):
      self.repoType = repoType
      self.baseTag = baseTag
      self.cvsRoot = cvsRoot
      # set a default localesURL accordingly to the repoType if none has been set 
      if repoType.find('hg') >= 0:
        if not localesFile:
          localesFile = "browser/locales/all-locales"          
        self.localesURL = "%s%s/raw-file/%s/%s" \
                          % (repo, branch, baseTag, localesFile)
      elif repoType.find('cvs') >= 0:
        if not localesFile:
          self.localesURL = "mozilla/browser/locales/all-locales"
        else:
          self.localesURL = localesFile
      # if the user wants to use something different than all locales
      # check ParseLocalesFile function to note that we now need a dictionary
      # with the locale as the key and a list of platform as the value for
      # each key to build a specific locale e.g. locales={'fr':['osx']}
      self.locales = locales
      self.tree = tree
      # Make sure a supported platform is passed. Allow variations, but make
      # sure to convert them to the form the locales files ues.
      assert platform in ('linux', 'win32', 'macosx', 'osx')
      self.platform = platform
      if self.platform == 'macosx':
        self.platform = 'osx'

  class NoMergeStamp(SourceStamp):
      """
      This source stamp implementation keeps them from being merged by
      the build master.
      """
      def canBeMergedWith(self, other):
        return False
  
  def _cbLoadedLocales(self, locales, reason=None, set_props=None):
      """
      This is the callback function that gets called once the list
      of locales are ready to be processed
      Let's fill the queues per builder and submit the BuildSets per each locale
      """
      log2.msg("L10nMixin:: loaded locales' list")
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
        props.update(dict(locale=locale),"Scheduler")
        props.setProperty("en_revision",self.baseTag,"Scheduler")
        props.setProperty("l10n_revision",self.baseTag,"Scheduler")
        log2.msg('Submitted '+locale+' locale')
        # let's submit the BuildSet for this locale
        self.submitBuildSet(
            buildset.BuildSet(self.builderNames,
                              self.NoMergeStamp(branch=self.branch),
                              reason,
                              properties = props))

  def getLocales(self): 
      """
      It returns a list of locales if the user has set a list of locales
      in the scheduler OR it returns a Deferred.
      
      You want to call this method via defer.maybeDeferred().
      """
      if self.locales:
        log2.msg('L10nMixin.getLocales():: The user has set a list of locales')
        return self.locales
      else:
        log2.msg("L10nMixin:: Getting locales from: "+self.localesURL)
        # we expect that getPage will return the output of "all-locales"
        # or "shipped-locales" or any file that contains a locale per line
        # in the begining of the line e.g. "en-GB" or "ja linux win32"
        if self.repoType == 'cvs':
           args = ['-q', '-d', self.cvsRoot, 'co', '-p', self.localesURL]
           env = {'CVS_RSH': 'ssh'}
           d = utils.getProcessOutput('cvs', args, env)
           d.addCallback(lambda data: ParseLocalesFile(data))
           return d
        else: # the repoType is 'hg'
          # getPage returns a defered that will return a string
          d = getPage(self.localesURL, timeout = 5 * 60)
          d.addCallback(lambda data: ParseLocalesFile(data))
          return d

  def createL10nBuilds(self, reason=None, set_props=None):
      """
      We request to get the locales that we have to process and which
      method to call once they are ready
      """
      log2.msg('L10nMixin:: A list of locales is going to be requested')
      d = defer.maybeDeferred(self.getLocales)
      d.addCallback(self._cbLoadedLocales, reason=reason, set_props=set_props)
      return d


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
        locales[locale] = buildPlatforms
    return locales


class TriggerableL10n(Triggerable, L10nMixin):
  """
  TriggerableL10n is used to paralellize the generation of l10n builds.

  TriggerableL10n is designed to be used with a Build factory that gets the
  locale to build from the 'locale' build property.
  """

  compare_attrs = ('name', 'builderNames', 
                   'minute', 'hour', 'dayOfMonth', 'month',
                   'dayOfWeek', 'branch')
  
  def __init__(self, name,  builderNames, repoType, platform, 
               minute=0, hour='*', dayOfMonth='*', month='*', dayOfWeek='*', 
               repo = 'http://hg.mozilla.org/', branch=None, baseTag='default',
               localesFile=None, cvsRoot=DEFAULT_CVSROOT, locales=None,
               tree="notset"):
    self.branch = branch 
    self.reason = None 
    L10nMixin.__init__(self, platform = platform, repoType = repoType,
                       repo = repo, branch = branch, baseTag = baseTag,
                       localesFile = localesFile, cvsRoot = cvsRoot,
                       locales = locales, tree = tree)
    Triggerable.__init__(self, name, builderNames)

  def trigger(self, ss, set_props=None):
      reason="This build was triggered by the successful completion of the en-US nightly."
      self.createL10nBuilds(reason=reason, set_props=set_props)


class DependentL10n(Dependent, L10nMixin):
  """
  This scheduler runs some set of 'downstream' builds when the
  'upstream' scheduler has completed successfully.
  """

  compare_attrs = ('name', 'upstream', 'builders')

  def __init__(self, name, upstream, builderNames, platform,
               repoType, repo = 'http://hg.mozilla.org/', branch=None,
               baseTag='default', localesFile=None,
               cvsRoot=DEFAULT_CVSROOT, locales=None, tree="notset"):
      Dependent.__init__(self, name, upstream, builderNames)
      # The next two lines has been added because of:
      # _cbLoadedLocales's BuildSet submit needs them
      self.branch = None 
      self.reason = None
      L10nMixin.__init__(self, platform = platform, repoType = repoType,
                         branch = branch, baseTag = baseTag,
                         localesFile = localesFile, cvsRoot = cvsRoot,
                         locales = locales, tree = tree)

  # ss is the source stamp that we don't use currently
  def upstreamBuilt(self, ss):
      self.createL10nBuilds()


class NightlyL10n(TriggerableL10n):
    ''' This is a thin wrapper around the TriggerableL10n class to make 
    using the class in the nightly context -- our standard use case --
    more clear. 
    '''
    def __init__(self, **kwargs):
        TriggerableL10n.__init__(self, **kwargs)
