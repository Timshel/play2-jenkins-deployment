#!/usr/bin/python
#
# author: Mariot Chauvin <mch@zenexity.com>
#
# A python script to deploy automatically play2 applications when a new green/blue build is available from Jenkins.
# The script polls Jenkins through its json api, and store the last build number to check if it needs to redeploy.
#
#  The script apply database evolutions automatically without further notice !
#
#  2 - Connection failed to Jenkins server
#  3 - JSON parsing failed
#  4 - JSON does not contain expected datas
#  5 - Preparation of the artifact failed

import sys, os, signal, errno, subprocess, os.path, shutil
import time
import urllib2, json
from ConfigParser import SafeConfigParser
import collections

# reading configuration outside of a function context
# to have always access to it

parser = SafeConfigParser()
parser.read('deployment.conf')

server = parser.get('jenkins', 'server')
jobname = parser.get('jenkins', 'jobname')
user = parser.get('jenkins',  'user')
token = parser.get('jenkins', 'token')
poll_delay = parser.get('jenkins', 'poll_delay')

path_env = os.path.abspath(parser.get('environement', 'path_env'))
path_running = os.path.join(path_env, parser.get('environement', 'path_running'))
path_building = os.path.join(path_env, parser.get('environement', 'path_building'))

app_name = parser.get('application', 'app_name')
app_opts = parser.get('application', 'java_opts')
app_port = parser.get('application', 'port')
app_apply_evolutions = parser.get('application', 'apply_evolutions')
app_conf_resource = ( parser.get('application', 'conf_resource') == "true" )
app_conf_file = parser.get('application', 'conf_file')

app_logger = parser.has_option('application', 'logger_file')

if( app_logger ):
    app_logger_file = parser.get('application', 'logger_file')
else:
    app_logger_file = ""

def main():

    print ""
    print "\t -- Play2 continuous deployment  --"
    print ""

    #Quit gracefully
    signal.signal(signal.SIGINT, quit)
    signal.signal(signal.SIGTERM, quit)

    #Loop until interruption or kill signals
    while True:

        need = needDeployment(server, jobname, user, token)

        if (need.value):
            print ""
            print "\t ~ Deployment of " + str(need.revision) + " ( Build " + str(need.number) + " ) "  + "start "
            print ""

            downloadArtifact(server, jobname, user, token, need.artifact)
            prepare("lastBuild.zip", app_name)
            deploy(app_apply_evolutions, app_name, app_opts, app_port, app_conf_resource, app_conf_file, app_logger, app_logger_file)

            updateLastDeployed(need.number)
            print ""
            print "\t ~  " + str(need.revision) + " has been successfuly deployed !"
            print ""

        elif not(isRunning("running")):
            print ""
            print "\t ~ Start of alreday checked out application start "
            print ""
            deploy(app_apply_evolutions, app_name, app_opts, app_port, app_conf_resource, app_conf_file, app_logger, app_logger_file)
            print ""
            print "\t ~ has been successfuly deployed !"
            print ""

        time.sleep(int(poll_delay));


def needDeployment(server, jobname, user, token):

    result = collections.namedtuple('value', ['revision', 'number', 'artifact'])
    result.value = False

    try:
        jsonBuildStatus = getBuildStatus(server, jobname, user, token)
        buildNumber = getBuildNumber(jsonBuildStatus)
        buildRevision = getBuildRevision(jsonBuildStatus)
        lastDeployed = getLastDeployed()

        result.revision = buildRevision
        result.number = buildNumber
        result.value = lastDeployed < buildNumber
        result.artifact = getArtifact(jsonBuildStatus)

    except (urllib2.HTTPError, urllib2.URLError) as e:
        print "\t ~ Error: Connection failed to  " + server + " with job name " + jobname + " - "

    except IOError as e:
        print "\t ~ Error: Could not find LASTDEPLOYED file"
        print e

    except Exception as e:
        print "\t ~ Error: Json parsing failed"
        print e

    return result

def isRunning(runPath):
    run = pidFile() and pidAlive(runningPid())
    return run

def quit(signum, frame):
    global process
    # we kill the serv when quitting.
    if 'process' in globals():
        os.killpg(process.pid, signal.SIGTERM)

    # When we quit we set back the last deployed to 0 this allow us to restart gracefully
    updateLastDeployed(0)
    print "\n\t -- Terminating --"

    sys.exit(0)

def connect(jenkinsUrl, user, token):
    req = urllib2.Request(jenkinsUrl)
    req.add_header('Authorization', encodeUserData(user, token))
    return urllib2.urlopen( req )

def getBuildStatus(server, jobname, user, token):
    jenkinsUrl = "http://{0}/job/{1}/lastSuccessfulBuild/api/json".format(server, jobname)
    jenkinsStream = connect(jenkinsUrl, user, token)
    return json.load(jenkinsStream)

def downloadArtifact(server, jobname, user, token, artifact):
    jenkinsUrl = "http://{0}/job/{1}/lastSuccessfulBuild/artifact/{2}".format(server, jobname, artifact)
    print "downloading " + jenkinsUrl
    jenkinsStream = connect(jenkinsUrl, user, token)

    # Open our local file for writing
    zipPath = os.path.join(path_env, "lastBuild.zip")
    with open(zipPath, "wb") as local_file:
        local_file.write(jenkinsStream.read())


# simple wrapper function to encode the username & pass
def encodeUserData(user, token):
    return "Basic " + (user + ":" + token).encode("base64").rstrip()

def getBuildNumber(buildStatusJson):
    if buildStatusJson.has_key( "number" ):
        return buildStatusJson["number"]
    else:
        raise Exception("\t ~ Error: Unable to get build number from JSON")

def getBuildRevision(buildStatusJson):
    if buildStatusJson.has_key( "actions" ):
        actions = buildStatusJson["actions"]
        for action in actions:
            if action.has_key("lastBuiltRevision"):
                return action["lastBuiltRevision"]["SHA1"]

    raise Exception("\t ~ Error: Unable to get build revision from JSON")

def getArtifact(buildStatusJson):
    if buildStatusJson.has_key( "artifacts" ):
        artifacts = buildStatusJson["artifacts"]
        if artifacts[0].has_key("relativePath"):
            return artifacts[0]["relativePath"]

    raise Exception("\t ~ Error: Unable to get artifact from JSON")

def getLastDeployed():
    file = open("LASTDEPLOYED", "r")
    content = file.read()
    lastDeployed = int(content)
    file.close()
    return lastDeployed

def updateLastDeployed(buildNumber):
    file = open("LASTDEPLOYED", "w")
    file.write(str(buildNumber))
    file.close()

def checkReturn(returnCode, exitCode, message):
    if( returnCode != 0 ):
        print "\t ~ Error: " + message
        sys.exit(exitCode)

def executeCommand(cmd, exitCode):
    s = subprocess.call(cmd, shell=True)
    checkReturn(s, exitCode, cmd)

def checkDeletePath(path):
    if( os.path.isdir(path) ):
        shutil.move(path, "/tmp/DELETE-{0}".format(jobname))
        shutil.rmtree("/tmp/DELETE-{0}".format(jobname))

def prepare(zipName, app_name):
    zipPath = os.path.join(path_env, zipName)
    if( os.path.isfile(zipPath) ):
        checkDeletePath(path_building)
        executeCommand("unzip -o {0} -d {1}".format(zipPath, path_building), 5)
        subFolderName = os.listdir(path_building)[0]
        executeCommand("mv {0}/{1}/* {0}".format(path_building, subFolderName), 5)
        executeCommand("chmod +x {0}/bin/{1}".format(path_building, app_name), 5)
    else:
        raise Exception("\t ~ Error: Missing archive : {0}".format(zipPath))

def switch():
    if( os.path.isdir(path_building) ):
        checkDeletePath(path_running)
        shutil.move(path_building, path_running)

# default strategy, kill and restart, is very basic and will result in downtime
# we could do far better with haproxy
# and 2 servers to have zero downtime
def killApp():
    try:
        if( pidFile() ):
            pid = runningPid()
            if pidAlive(pid):
                os.kill(pid, signal.SIGTERM)
                #leave 3 seconds to terminate properly
                time.sleep(3)
                if pidAlive(pid):
                    os.kill(pid, signal.SIGKILL)
            else:
                # No running instance to term or kill
                # we need to remove the file if there is one, otherwise play will not start
                deletePidFile()
    except IOError as e:
        # No PID file found, no need to worry
        pass

def deploy(app_apply_evolutions, app_name, app_opts, app_port, app_conf_resource, app_conf_file, app_logger, app_logger_file):
    global process

    killApp()
    switch()

    cmd = 'JAVA_OPTS="{0}" {1}/bin/{2} -DapplyEvolutions.default={3} -Dhttp.port={4}'.format(app_opts, path_running, app_name, app_apply_evolutions, app_port)

    if( app_conf_resource ):
        cmd = "{0} -Dconfig.resource={1}".format(cmd, app_conf_file)
    else:
        cmd = "{0} -Dconfig.file={1}".format(cmd, app_conf_file)

    if( app_logger ):
        cmd = "{0} -Dlogger.resource={1}".format(cmd, app_logger_file)

    process = subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid)

def runningPid():
    file = open(os.path.join(path_running, "RUNNING_PID"), "r")
    content = file.read()
    pid = int(content)
    file.close()
    return pid

def pidAlive(pid):
    try:
        os.kill(pid, 0)
    except OSError, err:
        if err.errno == errno.ESRCH:
            return False
    return True

def pidFile():
    return os.path.isfile(os.path.join(path_running, "RUNNING_PID"))

def deletePidFile():
    os.remove(os.path.join(path_running, "RUNNING_PID"))

main()
