#!/usr/bin/env python
import urlparse, urllib, time
try:
    import json
except:
    import simplejson as json

import httplib, urllib2, socket, ssl

import subprocess
from buildbotcustom.changes.hgpoller import _parse_changes
import logging as log

def buildValidatingOpener(ca_certs):
    class VerifiedHTTPSConnection(httplib.HTTPSConnection):
        def connect(self):
            # overrides the version in httplib so that we do
            #    certificate verification
            sock = socket.create_connection((self.host, self.port),
                                            self.timeout)
            if self._tunnel_host:
                self.sock = sock
                self._tunnel()

            # wrap the socket using verification with the root
            #    certs in trusted_root_certs
            self.sock = ssl.wrap_socket(sock,
                                        self.key_file,
                                        self.cert_file,
                                        cert_reqs=ssl.CERT_REQUIRED,
                                        ca_certs=ca_certs,
                                        )

    # wraps https connections with ssl certificate verification
    class VerifiedHTTPSHandler(urllib2.HTTPSHandler):
        def __init__(self, connection_class=VerifiedHTTPSConnection):
            self.specialized_conn_class = connection_class
            urllib2.HTTPSHandler.__init__(self)

        def https_open(self, req):
            return self.do_open(self.specialized_conn_class, req)

    https_handler = VerifiedHTTPSHandler()
    url_opener = urllib2.build_opener(https_handler)

    return url_opener

def validating_https_open(url, ca_certs, username=None, password=None):
    url_opener = buildValidatingOpener(ca_certs)
    req = urllib2.Request(url)
    if username and password:
        # Basic HTTP auth
        # The username/password aren't sent if the cert validation fails
        pw = ("%s:%s" % (username, password)).encode("base64").strip()
        req.add_header("Authorization", "Basic %s" % pw)
    return url_opener.open(req)

def getChanges(base_url, last_changeset=None, tips_only=False, ca_certs=None,
        username=None, password=None):
    bits = urlparse.urlparse(base_url)
    if bits.scheme == 'https':
        assert ca_certs, "you must specify ca_certs"

    params = [('full', '1')]
    if last_changeset:
        params.append( ('fromchange', last_changeset) )
    if tips_only:
        params.append( ('tipsonly', '1') )
    url = "%s/json-pushes?%s" % (base_url, urllib.urlencode(params))

    log.debug("Fetching %s", url)

    if bits.scheme == 'https':
        handle = validating_https_open(url, ca_certs, username, password)
    else:
        handle = urllib2.urlopen(url)

    data = handle.read()
    return _parse_changes(data)

def sendchange(master, branch, change):
    log.info("Sendchange %s to %s on branch %s", change['changeset'], master, branch)
    cmd = ['retry.py', '-r', '5', '-s', '5', '-t', '30',
            '--stdout-regexp', 'change sent successfully']
    cmd.extend(
          ['buildbot', 'sendchange',
            '--master', master,
            '--branch', branch,
            '--comments', change['comments'].encode('ascii', 'replace'),
            '--revision', change['changeset'],
            '--user', change['author'].encode('ascii', 'replace'),
            '--when', str(change['updated']),
            ])
    cmd.extend(change['files'])
    subprocess.check_call(cmd)

def processBranch(branch, state, config):
    log.debug("Processing %s", branch)
    master = config.get('main', 'master')
    if branch not in state:
        state[branch] = {'last_run': 0, 'last_changeset': None}
    branch_state = state[branch]
    interval = config.getint(branch, 'interval')
    if time.time() < (branch_state['last_run'] + interval):
        log.debug("Skipping %s, too soon since last run", branch)
        return

    branch_state['last_run'] = time.time()

    url = config.get(branch, 'url')
    ca_certs = config.get(branch, 'ca_certs')
    tips_only = config.getboolean(branch, 'tips_only')
    username = config.get(branch, 'username')
    password = config.get(branch, 'password')
    last_changeset = branch_state['last_changeset']

    try:
        changes = getChanges(url, tips_only=tips_only,
                last_changeset=last_changeset, ca_certs=ca_certs,
                username=username, password=password)
        # Do sendchanges!
        for c in changes:
            # Ignore off-default branches
            if c['branch'] != 'default' and config.getboolean(branch, 'default_branch_only'):
                log.info("Skipping %s on branch %s", c['changeset'], c['branch'])
                continue
            # Change the comments to include the url to the revision
            c['comments'] += ' %s/rev/%s' % (url, c['changeset'])
            sendchange(master, branch, c)

    except urllib2.HTTPError, e:
        msg = e.fp.read()
        if e.code == 500 and 'unknown revision' in msg:
            log.info("%s Repo was reset, resetting last_changeset", branch)
            branch_state['last_changeset'] = None
            return
        else:
            raise

    if not changes:
        # Empty repo, or no new changes; nothing to do
        return

    last_change = changes[-1]
    branch_state['last_changeset'] = last_change['changeset']

if __name__ == '__main__':
    from ConfigParser import RawConfigParser
    from optparse import OptionParser

    parser = OptionParser()
    parser.set_defaults(
            config_file="hgpoller.ini",
            verbosity=log.INFO,
            )
    parser.add_option("-f", "--config-file", dest="config_file")
    parser.add_option("-v", "--verbose", dest="verbosity", action="store_const", const=log.DEBUG)

    options, args = parser.parse_args()

    log.basicConfig(format="%(message)s", level=options.verbosity)

    config = RawConfigParser({
        'tips_only': 'no',
        'username': None,
        'password': None,
        'ca_certs': None,
        'interval': 300,
        'state_file': 'state.json',
        'default_branch_only': "yes",
        })
    config.read(options.config_file)

    try:
        state = json.load(open(config.get('main', 'state_file')))
    except (IOError, ValueError):
        state = {}

    branches = [s for s in config.sections() if s != 'main']
    for branch in branches:
        processBranch(branch, state, config)

    # Save state
    json.dump(state, open(config.get('main', 'state_file'), 'w'))
