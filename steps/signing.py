from urllib import urlencode
from random import shuffle

from OpenSSL.SSL import Context, TLSv1_METHOD, VERIFY_PEER,\
     VERIFY_FAIL_IF_NO_PEER_CERT, OP_NO_SSLv2
from OpenSSL.crypto import load_certificate, FILETYPE_PEM

from twisted.python.urlpath import URLPath
from twisted.internet.ssl import ContextFactory
from twisted.web.client import getPage
from twisted.python.failure import Failure
from twisted.internet import reactor
from twisted.python import log
from buildbot.steps.transfer import StringDownload


class HTTPSVerifyingContextFactory(ContextFactory):
    isClient = True

    def __init__(self, hostname, certfile):
        self.hostname = hostname
        data = open(certfile).read()
        self.cert = load_certificate(FILETYPE_PEM, data)

    def getContext(self):
        ctx = Context(TLSv1_METHOD)
        store = ctx.get_cert_store()
        store.add_cert(self.cert)
        ctx.set_verify(VERIFY_PEER | VERIFY_FAIL_IF_NO_PEER_CERT,
                       self.verifyHostname)
        ctx.set_options(OP_NO_SSLv2)
        return ctx

    def verifyHostname(self, connection, x509, errno, depth, preverifyOK):
        if preverifyOK:
            if self.hostname == x509.get_subject().commonName:
                return False
        return preverifyOK


class SigningServerAuthenication(StringDownload):
    current_attempt = 0
    stdio_log = None
    uri = None
    username = None
    password = None
    d = None
    interrupted = False

    def __init__(self, servers, server_cert, duration=6*3600, attempts=5,
                 sleeptime=60, **kwargs):
        kwargs['s'] = ''
        StringDownload.__init__(self, **kwargs)
        self.addFactoryArguments(servers=servers, server_cert=server_cert,
                                 duration=duration)
        self.servers = list(servers)
        shuffle(self.servers)
        self.server_cert = server_cert
        self.duration = duration
        self.attempts = attempts
        self.sleeptime = sleeptime

    def generateHeaders(self, method, credentials):
        headers = {}
        if method == 'POST':
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
        base64string = '%s:%s' % (credentials[0], credentials[1])
        base64string = base64string.encode("base64").strip()
        headers['Authorization'] = 'Basic %s' % base64string
        return headers

    def start(self):
        if self.interrupted:
            self.failed(Failure(Exception('Interrupted')))
            return

        self.current_attempt += 1

        if self.current_attempt > self.attempts:
            if len(self.servers) < 1:
                self.failed(Failure(Exception(
                    'No more signing servers to try.')))
            else:
                self.current_attempt = 1

        if self.current_attempt == 1:
            uri, self.username, self.password = self.servers.pop()
            self.uri = 'https://%s/token' % uri

        slaveName = self.getSlaveName()
        slaveIP = self.buildslave.slave.broker.transport.getPeer().host

        if not self.stdio_log:
            self.stdio_log = self.addLog('output')
            self.stdio_log.addHeader("Slave: %s\n" % slaveName)
            self.stdio_log.addHeader("IP: %s\n" % slaveIP)
            self.stdio_log.addHeader("Duration: %s\n" % self.duration)
        self.stdio_log.addStdout("URI: %s\n" % self.uri)

        method = 'POST'
        postdata = {
            'slave_ip': slaveIP,
            'duration': self.duration,
        }
        headers = self.generateHeaders(
            method=method,
            credentials=(self.username, self.password))
        contextFactory = HTTPSVerifyingContextFactory(
            URLPath(self.uri).netloc, self.server_cert)
        d = getPage(self.uri, method=method, headers=headers,
                    postdata=urlencode(postdata), contextFactory=contextFactory)
        d.addCallbacks(self.downloadSignature, self.requestFailed)

    def downloadSignature(self, res):
        self.s = res
        StringDownload.start(self)

    def interrupt(self, reason='Interrupted'):
        if not self.interrupted:
            self.interrupted = True
            StringDownload.interrupt(self, 'Interrupted')

    def requestFailed(self, err):
        msg = "%s: token generation failed, error message: %s" \
                % (self, err.getErrorMessage())
        log.msg(msg)
        if self.stdio_log:
            self.stdio_log.addStdout('%s\n' % msg)
        reactor.callLater(self.sleeptime, self.start)
