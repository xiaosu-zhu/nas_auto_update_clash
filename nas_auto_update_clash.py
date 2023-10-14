from __future__ import annotations

from typing import Any
import requests
import requests.compat
import logging
import yaml
import shutil
import pathlib
import os
import datetime
from joblib import Parallel, delayed
import json
import sys
import time
import schedule
import functools


LATENCY_TEST_TOLERANCE = 3

DEFAULT_HEADER = {
    "Accept": "*/*",
    "User-Agent": "Thunder Client (https://www.thunderclient.com)"
  }

class Updater:
    controllerRoot: str
    clashContainerConfigPath: str
    thisContainerConfigPath: str
    managedConfigUrl: str

    def __init__(self, controllerRoot, managedConfigUrl, clashContainerConfigPath, thisContainerConfigPath):
        logging.debug('Start Updater.__init__()')
        response = requests.get(controllerRoot)
        if not response.ok:
            raise ConnectionError('The give clash controller root return wrong status. GET %s: <%s> (%s)' % (controllerRoot, response.status_code, response.reason))

        if not os.path.exists(thisContainerConfigPath) and not os.path.isfile(thisContainerConfigPath):
            raise FileNotFoundError('The give clash config not found IN THIS RUNNING CONTAINER (not that clash container). Please check volum mount config. path: %s' % thisContainerConfigPath)

        self.controllerRoot = controllerRoot
        self.clashContainerConfigPath = clashContainerConfigPath
        self.thisContainerConfigPath = thisContainerConfigPath
        self.managedConfigUrl = managedConfigUrl

        self.downloadConfig()
        logging.debug('*DONE* Updater.__init__()')


    def downloadConfig(self):
        logging.debug('Start Updater.downloadConfig()')
        newConfig = requests.get(self.managedConfigUrl, headers=DEFAULT_HEADER).text
        try:
            yaml.safe_load(newConfig)
        except:
            logging.error("The given managed config url returns an invalid yaml. (%s)" % self.managedConfigUrl)
            raise
        logging.debug('*DONE* Updater.downloadConfig()')
        return newConfig

    def updateConfig(self):
        logging.debug('Start Updater.updateConfig()')
        # read the #!MANAGED-CONFIG shebang in config from `thisContainerConfigPath`
        with open(self.thisContainerConfigPath, "r") as fp:
            content = fp.readlines()


        shutil.copy(self.thisContainerConfigPath, pathlib.Path(self.thisContainerConfigPath).parent / "backup_config.yaml")
        with open(self.thisContainerConfigPath, "w") as fp:
            fp.write(self.downloadConfig())

        response = requests.put('/'.join([self.controllerRoot, 'configs']), json.dumps({
            'path': self.clashContainerConfigPath
        }, ensure_ascii=False).encode('utf-8'), headers=DEFAULT_HEADER | {'Content-Type': 'application/json'})
        if response.ok:
            logging.info('Update config at %s.', datetime.datetime.now())
            logging.debug('*DONE* Updater.updateConfig()')
            return
        raise ConnectionError('Update config via RESTful api failed. url: %s, status: <%s> (%s).' % ('/'.join([self.controllerRoot, 'configs']), response.status_code, response.reason))

    def getAllProxies(self) -> dict[str, Any]:
        logging.debug('Start Updater.getAllProxies()')
        response = requests.get('/'.join([self.controllerRoot, 'proxies']), headers=DEFAULT_HEADER)
        if not response.ok:
            raise ConnectionError('Get proxies from %s failed. status: <%s> (%s).' % ('/'.join([self.controllerRoot, 'proxies']), response.status_code, response.reason))
        proxies = response.json()
        logging.debug('*DONE* Updater.getAllProxies()')
        return {name: detail for name, detail in proxies['proxies'].items() if detail['type'] == 'Shadowsocks'}

    def testLatency(self) -> list[tuple[str, int]]:
        logging.debug('Start Updater.testLatency()')
        proxies = self.getAllProxies()

        def singleDelay(proxyName) -> tuple[str, int] | tuple[str, None]:
            totalLatency = 0

            for url in [r'http://www.themoviedb.org', r'http://webservice.fanart.tv', r'http://api.telegram.org']:
                testPass = False
                for _ in range(LATENCY_TEST_TOLERANCE):
                    response = requests.get('/'.join([self.controllerRoot, 'proxies', proxyName, 'delay']),
                        {
                            'timeout': 2000,
                            'url': url
                        }, headers=DEFAULT_HEADER)
                    if response.ok:
                        totalLatency += response.json()['delay']
                        testPass = True
                        break
                    elif not response.status_code == 408:
                        logging.debug('The latency test of %s is error. <%s> (%s)', proxyName, response.status_code, response.reason)
                        return proxyName, None
                if not testPass:
                    return proxyName, None

            return proxyName, totalLatency

        results = Parallel(-1)(delayed(singleDelay)(name) for name in proxies.keys())

        if results is None:
            raise RuntimeError("Run joblib to query latencies meet error.")

        results = [(x[0], x[1]) for x in results if x[1] is not None]
        logging.debug('*DONE* Updater.testLatency()')
        return results

    def changeMode(self, mode):
        logging.debug('Start Updater.changeMode()')
        if mode not in {'Global', 'Rule', 'Direct'}:
            raise AttributeError(mode)

        response = requests.patch('/'.join([self.controllerRoot, 'configs']), json.dumps({
            'mode': mode
        }, ensure_ascii=False).encode('utf8'), headers=DEFAULT_HEADER | {'Content-Type': 'application/json'})
        response = requests.get('/'.join([self.controllerRoot, 'configs']), headers=DEFAULT_HEADER)
        if response.ok and response.json()['mode'].lower() == mode.lower():
            logging.debug('Change mode to %s ok.', mode)
            logging.debug('*DONE* Updater.changeMode()')
            return
        raise ConnectionError('Change mode to %s failed. Current mode: %s', mode, response.json()['mode'])

    def selectBest(self):
        logging.debug('Start Updater.selectBest()')
        proxyLatencies = self.testLatency()
        bestProxy = min(proxyLatencies, key=lambda x: x[1])[0]
        logging.debug('Preapare to change GLOBAL to the best tested proxy: [%s].', bestProxy)
        # since the test-passed proxies have access to all target url, force to use clash GLOBAL.
        response = requests.put('/'.join([self.controllerRoot, 'proxies', 'GLOBAL']), json.dumps({
            'name': bestProxy
        }, ensure_ascii=False).encode('utf8'), headers={'Content-Type': 'application/json'})
        if response.ok:
            logging.debug('Changed GLOBAL to the best tested proxy: [%s].', bestProxy)
            # make sure clash have been in the mode GLOBAL
            self.changeMode('Global')
            logging.debug('*DONE* Updater.selectBest()')
            return
        logging.debug('Select proxy failed. url: %s', response.url)
        raise ConnectionError('Select proxy [%s] via RESTful API error, <%s> (%s)' % (bestProxy, response.status_code, response.reason))


def tryGetEnvVar(name, default=None, strict=False):
    var = os.environ.get(name, default)
    if strict and var is None:
        raise EnvironmentError('Get ENV variable `%s` return None.' % name)
    logging.debug('ENV Var `%s="%s"`' % (name, var))
    return var


def catch_exceptions(cancel_on_failure=False):
    def catch_exceptions_decorator(job_func):
        @functools.wraps(job_func)
        def wrapper(*args, **kwargs):
            try:
                return job_func(*args, **kwargs)
            except:
                import traceback
                logging.error(traceback.format_exc())
                if cancel_on_failure:
                    return schedule.CancelJob
        return wrapper
    return catch_exceptions_decorator


def updateConfig(updater: Updater):
    try:
        updater.updateConfig()
    except:
        logging.error('Update config failed. Skip for this time.')
        raise

def checkProxy(updater: Updater):
    try:
        updater.selectBest()
    except:
        logging.error('Select best proxy failed. Skip for this time.')
        raise

if __name__ == '__main__':
    level = logging.INFO if int(os.environ.get('VERBOSE', '0')) < 1 else logging.DEBUG

    logging.basicConfig(level=level, format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s', datefmt='%a, %d %b %Y %H:%M:%S', stream=sys.stdout, filemode='w')

    controllerRoot = tryGetEnvVar('CLASH_CONTROLLER_ROOT', strict=True)
    clashConfigPath = tryGetEnvVar('CLASH_CONFIG_PATH', strict=True)
    containerConfigPath = tryGetEnvVar('SELF_CONFIG_PATH', strict=True)
    managedConfigUrl = tryGetEnvVar('MANAGED_CONFIG_URL', strict=True)

    proxyCheckInterval = int(tryGetEnvVar('PROXY_CHECK_INTERVAL', strict=True))
    configUpdateInterval = int(tryGetEnvVar('CONFIG_UPDATE_INTERVAL', strict=True))

    updater = Updater(controllerRoot, managedConfigUrl, clashConfigPath, containerConfigPath)

    logging.info('Sucessfully created updater.')

    schedule.every(proxyCheckInterval).seconds.do(checkProxy, updater)
    schedule.every(configUpdateInterval).seconds.do(updateConfig, updater)

    while True:
        schedule.run_pending()
        time.sleep(1)
