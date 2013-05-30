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
#  5 - Git clone or checkout failed
#  6 - Compilation of play2 application failed

import sys, os, signal, errno, subprocess, os.path
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
jobname_publish = parser.get('jenkins', 'jobname_publish')
user = parser.get('jenkins',  'user')
token = parser.get('jenkins', 'token')

poll_delay = parser.get('jenkins', 'poll_delay')

play_path = parser.get('play', 'path')

play_app_git = parser.get('application', 'git')
play_app_git_publish = parser.get('application', 'git_publish')

play_app_path = parser.get('application', 'path')
play_app_path_publish = parser.get('application', 'path_publish')

play_app_port = parser.get('application', 'port')
play_app_apply_evolutions = parser.get('application', 'apply_evolutions')
play_app_conf_file = parser.get('application', 'conf_file')

play_app_logger = False
play_app_logger_file = ""

if (parser.has_option('application', 'logger_file')):
    play_app_logger = True
    play_app_logger_file = parser.get('application', 'logger_file')


env = parser.get('application', 'env')

def main():

    print ""
    print "\t -- Play2 continuous deployment  --"
    print ""

    #Quit gracefully
    signal.signal(signal.SIGINT, quit)
    signal.signal(signal.SIGTERM, quit)

    #Loop until interruption or kill signals
    while True:

        need = needDeployment(server, jobname_publish, user, token, "LAST_PUBLISHED")
        if (need.value):
            print ""
            print "\t ~ Publish of " + str(need.revision) + " ( Build " + str(need.number) + " ) "  + "start "
            print ""

            #go in the work directory
            previous = os.getcwd()
            os.chdir(env)

            checkout(jobname_publish, play_app_git_publish, need.revision)
            publish(jobname_publish, play_path, play_app_path_publish)

            #go back in our current directory
            os.chdir(previous)

            updateLastDeployed("LAST_PUBLISHED", need.number,)
            print ""
            print "\t ~  " + str(need.revision) + " has been successfuly published !"
            print ""

        need = needDeployment(server, jobname, user, token, "LAST_DEPLOYED")
        if (need.value):
            print ""
            print "\t ~ Deployment of " + str(need.revision) + " ( Build " + str(need.number) + " ) "  + "start "
            print ""

            #go in the work directory
            previous = os.getcwd()
            os.chdir(env)

            checkout(jobname, play_app_git, need.revision)
            deploy(jobname, play_path, play_app_path, play_app_apply_evolutions, play_app_conf_file, play_app_port, play_app_logger, play_app_logger_file)

            #go back in our current directory
            os.chdir(previous)

            updateLastDeployed("LAST_DEPLOYED", need.number)
            print ""
            print "\t ~  " + str(need.revision) + " has been successfuly deployed !"
            print ""

        elif not(isRunning()):
            #go in the work directory
            previous = os.getcwd()
            os.chdir(env)

            print ""
            print "\t ~ Start of alreday checked out application start "
            print ""
            deploy(jobname, play_path, play_app_path, play_app_apply_evolutions, play_app_conf_file, play_app_port, play_app_logger, play_app_logger_file)
            print ""
            print "\t ~ has been successfuly deployed !"
            print ""

            #go back in our current directory
            os.chdir(previous)

        time.sleep(int(poll_delay));


def needDeployment(server, jobname, user, token, fileName):

    result = collections.namedtuple('value', ['revision', 'number'])
    result.value = False

    try:
        jsonBuildStatus = getBuildStatus(server, jobname, user, token)
        buildNumber = getBuildNumber(jsonBuildStatus)
        buildRevision = getBuildRevision(jsonBuildStatus)
        lastDeployed = getLastDeployed(fileName)

        result.revision = buildRevision
        result.number = buildNumber
        result.value = lastDeployed < buildNumber

    except (urllib2.HTTPError, urllib2.URLError) as e:
        print "\t ~ Error: Connection failed to  " + server + " with job name " + jobname + " - "

    except IOError as e:
        print "\t ~ Error: Could not find LASTDEPLOYED file"
        print e

    except Exception as e:
        print "\t ~ Error: Json parsing failed"
        print e

    return result


def checkedout():
    current = os.getcwd()
    p1 = os.path.join(current, env)
    p2 = os.path.join(p1, jobname)
    p3 = os.path.join(p2, play_app_path)
    return os.path.isdir(p3)

def isRunning():
    #go in the work directory
    previous = os.getcwd()
    os.chdir(env)
    os.chdir(jobname)
    os.chdir(play_app_path)

    run = pidFile() and pidAlive(runningPid())

    #go back in our current directory
    os.chdir(previous)
    return run

def quit(signum, frame):
    global process
    # we kill the serv when quitting.
    if 'process' in globals():
        os.killpg(process.pid, signal.SIGTERM)

    # when we quit we set back the last deployed to 0
    # this allow us to restart gracefully
    updateLastDeployed("LAST_PUBLISHED", 0)
    updateLastDeployed("LAST_DEPLOYED", 0)
    print "\n\t -- Terminating --"

    sys.exit(0)

def getBuildStatus(server, jobname, user, token):
    jenkinsStream = connect(server, jobname, user, token)
    return json.load(jenkinsStream)

def connect(server, jobname, user, token):
    jenkinsUrl = "http://" + server + "/job/" + jobname + "/lastSuccessfulBuild/api/json";
    req = urllib2.Request(jenkinsUrl)
    req.add_header('Authorization', encodeUserData(user, token))
    return urllib2.urlopen( req )

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
        if actions[1].has_key("lastBuiltRevision"):
            return actions[1]["lastBuiltRevision"]["SHA1"]
        elif  actions[2].has_key("lastBuiltRevision"):
            return actions[2]["lastBuiltRevision"]["SHA1"]

    raise Exception("\t ~ Error: Unable to get build revision from JSON")

def getLastDeployed(fileName):
    file = open(fileName, "r")
    content = file.read()
    lastDeployed = int(content)
    file.close()
    return lastDeployed

def updateLastDeployed(fileName, buildNumber):
    file = open(fileName, "w")
    file.write(str(buildNumber))
    file.close()

def checkout(jobname, play_app_git, buildRevision):
    if not os.path.exists(jobname):
        s = subprocess.call('git clone ' + play_app_git + ' ' + jobname, shell=True)
        if (s != 0):
            print "\t ~ Error: Git clone of " + play_app_git + " failed"
            sys.exit(5)
    previous = os.getcwd()
    os.chdir(jobname)
    subprocess.call('git fetch', shell=True)
    s = subprocess.call('git checkout -f ' + str(buildRevision), shell=True)
    if (s != 0):
        print "\t ~ Error: Git checkout of " + str(buildRevision) + " failed"
        sys.exit(5)
    os.chdir(previous)

def publish(jobname, play_path, play_app_path):
    previous = os.getcwd()
    os.chdir(jobname)
    os.chdir(play_app_path)
    s = subprocess.call(play_path + ' clean compile publish-local', shell=True)
    os.chdir(previous)

def deploy(jobname, play_path, play_app_path, play_app_apply_evolutions, play_app_conf_file, play_app_port, play_app_logger, play_app_logger_file):
    global process
    previous = os.getcwd()
    os.chdir(jobname)
    os.chdir(play_app_path)
    s = subprocess.call(play_path + ' clean compile stage', shell=True)
    if (s == 0):
        # default strategy, kill and restart, is very basic and will result in downtime
        # we could do far better with haproxy
        # and 2 servers to have zero downtime
        try:
            pid = runningPid()
            if pidAlive(pid):
                os.kill(pid, signal.SIGTERM)
                #leave 3 seconds to terminate properly
                time.sleep(3)
                os.kill(pid, signal.SIGKILL)
            else:
                # No running instance to term or kill
                if (pidFile()):
                    # we need to remove the file if there is one, otherwise play will not start
                    deletePidFile()
        except IOError as e:
            # No PID file found, no need to worry
            pass

        cmd = 'target/start -DapplyEvolutions.default=' + play_app_apply_evolutions + ' -Dconfig.file=' + play_app_conf_file +  ' -Dhttp.port='+play_app_port
        if (play_app_logger):
            cmd = cmd + ' -Dlogger.resource=' + play_app_logger_file
        process = subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid)
    else:
        # This should never happen as we retrieve only green builds
        print '\t ~ Error: Compilation failed !'
        sys.exit(6)
    os.chdir(previous)

def runningPid():
    file = open("RUNNING_PID", "r")
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
    return os.path.isfile("RUNNING_PID")

def deletePidFile():
    os.remove("RUNNING_PID")

main()
