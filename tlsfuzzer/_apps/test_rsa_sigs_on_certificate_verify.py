# Author: Hubert Kario, (c) 2016
# Released under Gnu GPL v2.0, see LICENSE file for details
"""Test for CVE-2015-7575 (SLOTH)"""

from __future__ import print_function
import traceback
import sys
import getopt
import re
from random import sample
from itertools import chain

from tlsfuzzer.runner import Runner
from tlsfuzzer.messages import Connect, ClientHelloGenerator, \
        ClientKeyExchangeGenerator, ChangeCipherSpecGenerator, \
        FinishedGenerator, ApplicationDataGenerator, \
        CertificateGenerator, CertificateVerifyGenerator, \
        AlertGenerator, TCPBufferingEnable, TCPBufferingDisable, \
        TCPBufferingFlush
from tlsfuzzer.expect import ExpectServerHello, ExpectCertificate, \
        ExpectServerHelloDone, ExpectChangeCipherSpec, ExpectFinished, \
        ExpectAlert, ExpectClose, ExpectCertificateRequest, \
        ExpectApplicationData, ExpectServerKeyExchange
from tlslite.extensions import SignatureAlgorithmsExtension, \
        SignatureAlgorithmsCertExtension, SupportedGroupsExtension
from tlslite.constants import CipherSuite, AlertDescription, \
        HashAlgorithm, SignatureAlgorithm, ExtensionType, GroupName
from tlslite.utils.keyfactory import parsePEMKey
from tlslite.x509 import X509
from tlslite.x509certchain import X509CertChain
from tlsfuzzer.helpers import RSA_SIG_ALL, AutoEmptyExtension
from tlsfuzzer.utils.lists import natural_sort_keys


version = 8


def help_msg():
    print("Usage: <script-name> [-h hostname] [-p port] [[probe-name] ...]")
    print(" -h hostname    name of the host to run the test against")
    print("                localhost by default")
    print(" -p port        port number to use for connection, 4433 by default")
    print(" probe-name     if present, will run only the probes with given")
    print("                names and not all of them, e.g \"sanity\"")
    print(" -e probe-name  exclude the probe from the list of the ones run")
    print("                may be specified multiple times")
    print(" -n num         run 'num' or all(if 0) tests instead of default(all)")
    print("                (excluding \"sanity\" tests)")
    print(" -x probe-name  expect the probe to fail. When such probe passes despite being marked like this")
    print("                it will be reported in the test summary and the whole script will fail.")
    print("                May be specified multiple times.")
    print(" -X message     expect the `message` substring in exception raised during")
    print("                execution of preceding expected failure probe")
    print("                usage: [-x probe-name] [-X exception], order is compulsory!")
    print(" -k keyfile     file with private key")
    print(" -c certfile    file with certificate of client")
    print(" -d             negotiate (EC)DHE instead of RSA key exchange")
    print(" -M | --ems     Enable support for Extended Master Secret")
    print(" --help         this message")


def main():
    """check if obsolete signature algorithm is rejected by server"""
    conversations = {}
    hostname = "localhost"
    port = 4433
    num_limit = None
    run_exclude = set()
    expected_failures = {}
    last_exp_tmp = None
    private_key = None
    cert = None
    dhe = False
    ems = False

    argv = sys.argv[1:]
    opts, argv = getopt.getopt(argv, "h:p:e:x:X:k:c:dMn:", ["help", "ems"])

    for opt, arg in opts:
        if opt == '-k':
            text_key = open(arg, 'rb').read()
            if sys.version_info[0] >= 3:
                text_key = str(text_key, 'utf-8')
            private_key = parsePEMKey(text_key, private=True)
        elif opt == '-c':
            text_cert = open(arg, 'rb').read()
            if sys.version_info[0] >= 3:
                text_cert = str(text_cert, 'utf-8')
            cert = X509()
            cert.parse(text_cert)
        elif opt == '-h':
            hostname = arg
        elif opt == '-p':
            port = int(arg)
        elif opt == '-e':
            run_exclude.add(arg)
        elif opt == '-n':
            num_limit = int(arg)
        elif opt == '-x':
            expected_failures[arg] = None
            last_exp_tmp = str(arg)
        elif opt == '-X':
            if not last_exp_tmp:
                raise ValueError("-x has to be specified before -X")
            expected_failures[last_exp_tmp] = str(arg)
        elif opt == '--help':
            help_msg()
            sys.exit(0)
        elif opt == '-d':
            dhe = True
        elif opt == '-M' or opt == '--ems':
            ems = True
        else:
            raise ValueError("Unknown option: {0}".format(opt))

    if argv:
        run_only = set(argv)
    else:
        run_only = None


    if not private_key:
        raise ValueError("Specify private key file using -k")
    if not cert:
        raise ValueError("Specify certificate file using -c")

    conversation = Connect(hostname, port)
    node = conversation
    if dhe:
        ciphers = [CipherSuite.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
                   CipherSuite.TLS_DHE_RSA_WITH_AES_128_CBC_SHA,
                   CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
    else:
        ciphers = [CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                   CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
    ext = {ExtensionType.signature_algorithms :
           SignatureAlgorithmsExtension().create([
                (getattr(HashAlgorithm, x),
                    SignatureAlgorithm.rsa) for x in ['sha512', 'sha384', 'sha256',
                        'sha224', 'sha1', 'md5']]),
           ExtensionType.signature_algorithms_cert :
           SignatureAlgorithmsCertExtension().create(RSA_SIG_ALL)}
    if dhe:
        ext[ExtensionType.supported_groups] = SupportedGroupsExtension()\
            .create([GroupName.secp256r1, GroupName.ffdhe2048])
    if ems:
        ext[ExtensionType.extended_master_secret] = AutoEmptyExtension()
    node = node.add_child(ClientHelloGenerator(ciphers, extensions=ext))
    node = node.add_child(ExpectServerHello(version=(3, 3)))
    node = node.add_child(ExpectCertificate())
    if dhe:
        node = node.add_child(ExpectServerKeyExchange())
    node = node.add_child(ExpectCertificateRequest())
    node = node.add_child(ExpectServerHelloDone())
    node = node.add_child(TCPBufferingEnable())
    node = node.add_child(CertificateGenerator(X509CertChain([cert])))
    node = node.add_child(ClientKeyExchangeGenerator())
    node = node.add_child(CertificateVerifyGenerator(private_key))
    node = node.add_child(ChangeCipherSpecGenerator())
    node = node.add_child(FinishedGenerator())
    node = node.add_child(TCPBufferingDisable())
    node = node.add_child(TCPBufferingFlush())
    node = node.add_child(ExpectChangeCipherSpec())
    node = node.add_child(ExpectFinished())
    node = node.add_child(ApplicationDataGenerator(b"GET / HTTP/1.0\n\n"))
    node = node.add_child(ExpectApplicationData())
    node = node.add_child(AlertGenerator(AlertDescription.close_notify))
    node = node.add_child(ExpectClose())
    node.next_sibling = ExpectAlert()
    node.next_sibling.add_child(ExpectClose())

    conversations["sanity"] = conversation

    for prf in ['sha256', 'sha384']:
        for md in ['sha1', 'sha256', 'sha384', 'sha512']:
            conversation = Connect(hostname, port)
            node = conversation
            if prf == 'sha256':
                if dhe:
                    ciphers = [CipherSuite.TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA,
                               CipherSuite.TLS_DHE_RSA_WITH_AES_128_CBC_SHA,
                               CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
                else:
                    ciphers = [CipherSuite.TLS_RSA_WITH_AES_128_CBC_SHA,
                               CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
            else:
                if dhe:
                    ciphers = [CipherSuite.TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384,
                               CipherSuite.TLS_DHE_RSA_WITH_AES_256_GCM_SHA384,
                               CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
                else:
                    ciphers = [CipherSuite.TLS_RSA_WITH_AES_256_GCM_SHA384,
                               CipherSuite.TLS_EMPTY_RENEGOTIATION_INFO_SCSV]
            ext = {ExtensionType.signature_algorithms :
                   SignatureAlgorithmsExtension().create([
                     (getattr(HashAlgorithm, x),
                      SignatureAlgorithm.rsa) for x in ['sha512', 'sha384', 'sha256',
                                                        'sha224', 'sha1', 'md5']]),
                   ExtensionType.signature_algorithms_cert :
                   SignatureAlgorithmsCertExtension().create(RSA_SIG_ALL)}
            if dhe:
                ext[ExtensionType.supported_groups] = \
                    SupportedGroupsExtension().create(
                        [GroupName.secp256r1, GroupName.ffdhe2048])
            if ems:
                ext[ExtensionType.extended_master_secret] = AutoEmptyExtension()
            node = node.add_child(ClientHelloGenerator(ciphers, extensions=ext))
            node = node.add_child(ExpectServerHello(version=(3, 3)))
            node = node.add_child(ExpectCertificate())
            if dhe:
                node = node.add_child(ExpectServerKeyExchange())
            node = node.add_child(ExpectCertificateRequest())
            node = node.add_child(ExpectServerHelloDone())
            node = node.add_child(TCPBufferingEnable())
            node = node.add_child(CertificateGenerator(X509CertChain([cert])))
            node = node.add_child(ClientKeyExchangeGenerator())
            node = node.add_child(CertificateVerifyGenerator(
                private_key, msg_alg=(getattr(HashAlgorithm, md), SignatureAlgorithm.rsa)))
            node = node.add_child(ChangeCipherSpecGenerator())
            node = node.add_child(FinishedGenerator())
            node = node.add_child(TCPBufferingDisable())
            node = node.add_child(TCPBufferingFlush())
            node = node.add_child(ExpectChangeCipherSpec())
            node = node.add_child(ExpectFinished())
            node = node.add_child(ApplicationDataGenerator(b"GET / HTTP/1.0\n\n"))
            node = node.add_child(ExpectApplicationData())
            node = node.add_child(AlertGenerator(AlertDescription.close_notify))
            node = node.add_child(ExpectClose())
            node.next_sibling = ExpectAlert()
            node.next_sibling.add_child(ExpectClose())

            conversations["check {0} w/{1} PRF".format(md, prf)] = \
                    conversation

    # run the conversation
    good = 0
    bad = 0
    xfail = 0
    xpass = 0
    failed = []
    xpassed = []
    if not num_limit:
        num_limit = len(conversations)

    sanity_tests = [('sanity', conversations['sanity'])]
    if run_only:
        if num_limit > len(run_only):
            num_limit = len(run_only)
        regular_tests = [(k, v) for k, v in conversations.items() if
                          k in run_only]
    else:
        regular_tests = [(k, v) for k, v in conversations.items() if
                         (k != 'sanity') and k not in run_exclude]
    sampled_tests = sample(regular_tests, min(num_limit, len(regular_tests)))
    ordered_tests = chain(sanity_tests, sampled_tests, sanity_tests)

    for c_name, c_test in ordered_tests:
        if run_only and c_name not in run_only or c_name in run_exclude:
            continue
        print("{0} ...".format(c_name))

        runner = Runner(c_test)

        res = True
        exception = None
        #because we don't want to abort the testing and we are reporting
        #the errors to the user, using a bare except is OK
        #pylint: disable=bare-except
        try:
            runner.run()
        except Exception as exp:
            exception = exp
            print("Error while processing")
            print(traceback.format_exc())
            res = False
        #pylint: enable=bare-except

        if c_name in expected_failures:
            if res:
                xpass += 1
                xpassed.append(c_name)
                print("XPASS-expected failure but test passed\n")
            else:
                if expected_failures[c_name] is not None and  \
                    expected_failures[c_name] not in str(exception):
                        bad += 1
                        failed.append(c_name)
                        print("Expected error message: {0}\n"
                            .format(expected_failures[c_name]))
                else:
                    xfail += 1
                    print("OK-expected failure\n")
        else:
            if res:
                good += 1
                print("OK\n")
            else:
                bad += 1
                failed.append(c_name)

    print("Test end")
    print(20 * '=')
    print("version: {0}".format(version))
    print(20 * '=')
    print("TOTAL: {0}".format(len(sampled_tests) + 2*len(sanity_tests)))
    print("SKIP: {0}".format(len(run_exclude.intersection(conversations.keys()))))
    print("PASS: {0}".format(good))
    print("XFAIL: {0}".format(xfail))
    print("FAIL: {0}".format(bad))
    print("XPASS: {0}".format(xpass))
    print(20 * '=')
    sort = sorted(xpassed ,key=natural_sort_keys)
    if len(sort):
        print("XPASSED:\n\t{0}".format('\n\t'.join(repr(i) for i in sort)))
    sort = sorted(failed, key=natural_sort_keys)
    if len(sort):
        print("FAILED:\n\t{0}".format('\n\t'.join(repr(i) for i in sort)))

    if bad or xpass:
        sys.exit(1)

if __name__ == "__main__":
    main()
