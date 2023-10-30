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
import json
import sys
import fastapi
import asyncio
import uvicorn
from rocketry import Rocketry

sched_logger = logging.getLogger("rocketry.scheduler")
sched_logger.addHandler(logging.StreamHandler(sys.stdout))

# Create Rocketry app
app_rocketry = Rocketry(execution="async", config={
    'silence_task_prerun': True,
    'silence_task_logging': True,
    'silence_cond_check': True
})
app_fastapi = fastapi.FastAPI()


class Server(uvicorn.Server):
    """Customized uvicorn.Server

    Uvicorn server overrides signals and we need to include
    Rocketry to the signals."""
    def handle_exit(self, sig: int, frame) -> None:
        app_rocketry.session.shut_down()
        return super().handle_exit(sig, frame)



LATENCY_TEST_TOLERANCE = 3

DEFAULT_HEADER = {
    "Accept": "application/octet-stream,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36"
}

class Updater:
    controllerRoot: str
    clashContainerConfigPath: str
    thisContainerConfigPath: str
    managedConfigUrl: str

    def __init__(self, controllerRoot, clashSecret, managedConfigUrl, clashContainerConfigPath, thisContainerConfigPath):
        logging.debug('Start Updater.__init__()')
        self.controllerRoot = controllerRoot
        self.clashContainerConfigPath = clashContainerConfigPath
        self.thisContainerConfigPath = thisContainerConfigPath
        self.managedConfigUrl = managedConfigUrl
        self.clashSecret = clashSecret

        self.clashHeader = DEFAULT_HEADER | { 'Authorization': f'Bearer {self.clashSecret}' } | {'Content-Type': 'application/json'}


        response = requests.get(controllerRoot, headers=self.clashHeader)
        if not response.ok:
            raise ConnectionError('The give clash controller root return wrong status. GET %s: <%s> (%s)' % (controllerRoot, response.status_code, response.reason))

        if not os.path.exists(thisContainerConfigPath) and not os.path.isfile(thisContainerConfigPath):
            raise FileNotFoundError('The give clash config not found IN THIS RUNNING CONTAINER (not that clash container). Please check volum mount config. path: %s' % thisContainerConfigPath)


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

    def modifyConfig(self, configStr: str) -> str:
        config = yaml.safe_load(configStr)
        config['external-controller'] = '127.0.0.1:9090'
        config['secret'] = self.clashSecret
        config['allow-lan'] = True
        return yaml.safe_dump(config)

    def updateConfig(self):
        logging.debug('Start Updater.updateConfig()')


        shutil.copy(self.thisContainerConfigPath, pathlib.Path(self.thisContainerConfigPath).parent / "backup_config.yaml")
        with open(self.thisContainerConfigPath, "w") as fp:
            fp.write(self.modifyConfig(self.downloadConfig()))

        response = requests.put('/'.join([self.controllerRoot, 'configs']) + '?force=true', json.dumps({
            'path': self.clashContainerConfigPath
        }, ensure_ascii=False).encode('utf-8'), headers=self.clashHeader)
        if response.ok:
            logging.info('Update config at %s.', datetime.datetime.now())
            logging.debug('*DONE* Updater.updateConfig()')
            return
        raise ConnectionError('Update config via RESTful api failed. url: %s, status: <%s> (%s).' % ('/'.join([self.controllerRoot, 'configs']), response.status_code, response.reason))

    def getAllProxies(self) -> dict[str, Any]:
        logging.debug('Start Updater.getAllProxies()')
        response = requests.get('/'.join([self.controllerRoot, 'proxies']), headers=self.clashHeader)
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
                        }, headers=self.clashHeader)
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

        results = [singleDelay(name) for name in proxies.keys()]

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
        }, ensure_ascii=False).encode('utf8'), headers=self.clashHeader)
        response = requests.get('/'.join([self.controllerRoot, 'configs']), headers=self.clashHeader)
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
        }, ensure_ascii=False).encode('utf8'), headers=self.clashHeader)
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


async def main():

    level = logging.INFO if int(os.environ.get('VERBOSE', '0')) < 1 else logging.DEBUG

    logging.basicConfig(level=level, format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s', datefmt='%a, %d %b %Y %H:%M:%S', stream=sys.stdout, filemode='w')

    controllerRoot = tryGetEnvVar('CLASH_CONTROLLER_ROOT', strict=True)
    clashConfigPath = tryGetEnvVar('CLASH_CONFIG_PATH', strict=True)
    containerConfigPath = tryGetEnvVar('SELF_CONFIG_PATH', strict=True)
    managedConfigUrl = tryGetEnvVar('MANAGED_CONFIG_URL', strict=True)
    clashSecret = tryGetEnvVar('CLASH_SECRET', strict=True)

    updater = Updater(controllerRoot, clashSecret, managedConfigUrl, clashConfigPath, containerConfigPath)

    logging.info('Sucessfully created updater.')



    @app_rocketry.task('every 72 hours')
    async def updateConfig():
        try:
            updater.updateConfig()
            logging.info('Update config complete. Next scheduled task starts at %s', datetime.datetime.now() + datetime.timedelta(seconds=72*3600))
        except:
            logging.error('Update config failed. Skip for this time.')
            raise

    @app_rocketry.task('every 8 hours')
    async def checkProxy():
        try:
            updater.selectBest()
            logging.info('Select best proxy complete. Next scheduled task starts at %s', datetime.datetime.now() + datetime.timedelta(seconds=8*3600))
        except:
            logging.error('Select best proxy failed. Skip for this time.')
            raise


    @app_fastapi.get('/update-config')
    async def rest_update_config(background_tasks: fastapi.BackgroundTasks):
        def func():
            updater.updateConfig()
            logging.info('Update config complete.')
        background_tasks.add_task(func)
        return {'message': 'called update_config().'}

    @app_fastapi.get('/check-proxy')
    async def rest_checkProxy(background_tasks: fastapi.BackgroundTasks):
        def func():
            updater.selectBest()
            logging.info('Select best proxy complete.')
        background_tasks.add_task(func)
        return {'message': 'called rest_checkProxy().'}


    "Run scheduler and the API"
    server = Server(config=uvicorn.Config(app_fastapi, workers=1, loop="asyncio"))

    api = asyncio.create_task(server.serve())
    sched = asyncio.create_task(app_rocketry.serve())

    await asyncio.wait([sched, api])


if __name__ == "__main__":
    asyncio.run(main())
