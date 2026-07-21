#!/bin/bash -l

is_bash=$(ps -p $$ | grep bash | wc -l)
if [ ${is_bash} -eq 0 ]; then
  echo "Please use bash to start Agent, e.g. bash bin/$(basename $0)"
  exit 1
fi
#k,o,q,u,y,z
while getopts 'a:s:r:f:n:c:w:p:t:l:s:x:d:g:j:1:2:3:4:b:e:h:i:v:z:m:u:o:5:6:7:' opt; do
  case $opt in
  a)
    access_key="$OPTARG"
    ;;
  s)
    secret_access_key="$OPTARG"
    ;;
  r)
    region_id="$OPTARG"
    ;;
  f)
    x_project_id="$OPTARG"
    ;;
  n)
    slave_name="$OPTARG"
    ;;
  c)
    cluster_id="$OPTARG"
    ;;
  w)
    work_dir="$OPTARG"
    ;;
  p)
    endpoint="$OPTARG"
    ;;
  t)
    x_auth_token="$OPTARG"
    ;;
  l)
    label="$OPTARG"
    ;;
  s)
    mem_min="$OPTARG"
    ;;
  x)
    mem_max="$OPTARG"
    ;;
  d)
    install_docker="$OPTARG"
    ;;
  g)
    install_git="$OPTARG"
    ;;
  j)
    install_jdk="$OPTARG"
    ;;
  1)
    user_name="$OPTARG"
    ;;
  2)
    user_id="$OPTARG"
    ;;
  3)
    domain_name="$OPTARG"
    ;;
  4)
    domain_id="$OPTARG"
    ;;
  b)
    need_docker="$OPTARG"
    ;;
  h)
    obs_domain_name="$OPTARG"
    ;;
  i)
    inner_user="$OPTARG"
    ;;
  v)
    agent_version="$OPTARG"
    ;;
  z)
    external_global_domain_name="$OPTARG"
    ;;
  m)
    hcs="$OPTARG"
    ;;
  u)
    user_check="$OPTARG"
    ;;
  o)
    register_timer="$OPTARG"
    ;;
  5)
    auth_secret="$OPTARG"
    ;;
  6)
    cluster_resource_type="$OPTARG"
    ;;
  7)
    ip="$OPTARG"
    ;;
  ?)
    echo -e "Usage: $(basename $0) -${codearts}\n
    -a:    you access ey (get from 'My Credential') : Required\n
    -s:    you secret access keyget from 'My Credential') : Required\n
    -r:    cloud region id : Required\n
    -c:    the cluster id : Required\n
    -n:    slave agent name : Required\n
    -w:    slave work_dir : Required\n
    -h:    agent obs domain name: Required\n
    -p:    the server host, IP:PORT or domain : Optional\n
    -t:    the auth token(If ak / sk is already set, the x-auth-token can be empty.): codeartsional\n
    -l:    slave label : Optional\n
    -s:    means min_memory default 256m: Optional\n
    -x:    means max_memory default 512m: Optional\n
    -d:    enable auto need install docker: Optional\n
    -g:    enable auto need install git: Optional\n
    -b:    enable check docker: Optional\n
    -z:    external global domain name: Optional\n
    -u:    whether restrict root user to start: Optional\n
    -j:    enable auto need install java: Optional\n
    -5:    auth secret(If ak sk is already set, the auth secret will be ignored.): Optional\n
    -6:    cluster resource type, self-hosted | exclusive: Optional\n
    -7:    ip: host ip Optional\n
    -o:    whether to register as a timer, default false: Optional"
    exit
    ;;
  esac
done

function writeLog() {
  msg="$1\n"
  printf "[$(date '+%Y-%m-%d %H:%M:%S')] $msg" | tee -a ${OCTOPUS_AGENT_LOG}
}

function exit_check() {
  code=$1
  msg=$2
  if [[ "$code" != "0" ]]; then
    writeLog "[ERROR] $msg"
    exit ${code}
  fi
}

#下载
function download() {
  url=$1
  target=$2
  writeLog "[INFO] Download URL:${url}"
  if [[ -f $(which curl) ]]; then
    curl -# -o ${target} -k ${url}
    exit_check $? "Download failed!"
  else
    wget --no-check-certificate ${url} -O target
    exit_check $? "Download failed!"
  fi
}

function check_slave_name() {
  if [[ -z ${slave_name} ]]; then
    exit_check 1 "slave-name is blank"
  else
    if [[ ! ${slave_name} =~ ^[a-zA-Z0-9._-]{1,50}$ ]]; then
      exit_check 1 " slave-name is invalid"
    fi
  fi
}

#检查cluster_id
function check_cluster_id() {
  if [[ -z ${cluster_id} ]]; then
    exit_check 1 "cluster-id is blank"
  else
    if [[ ! ${cluster_id} =~ ^[A-Za-z0-9]{32}$ ]]; then
      exit_check 1 "cluster-id is invalid"
    fi
  fi
}

#检查endpoint
function check_endpoint() {
  if [[ -z ${endpoint} ]]; then
    writeLog "[INFO]endpoint is blank"
  fi
}

#检查token
function check_token() {
  if [[ -z ${x_auth_token} ]]; then
    writeLog "[INFO]x_auth_token is blank"
  fi
}

#检查check_label
function check_label() {
  if [[ -z ${label} ]]; then
    label=${cluster_id}
  fi
}

# check检查slave用户
function check_user_slave() {
  USER=$(whoami)
  #用户必须是root
  if [[ ${USER} != "root" ]]; then
    exit_check 1 "User [${USER}] is not root, please use root user start."
  fi
}

#检查workDir
function check_workdir() {
  if [[ -z ${work_dir} ]]; then
    #work_dir为空
    exit_check 1 "workDir is not allow blank ."
  fi
  if [[ ! -d ${work_dir} ]]; then
    #work_dir目录不存在,则创建目录
    writeLog "[WARN] work_dir is not exsit."
    #创建目录
    mkdir -p ${work_dir}/remoting/logs
  fi
}

#检查认证参数
function check_ak_sk() {
  #ak、sk为空或不符合正则
  if [[ ! -z ${access_key} && ! ${access_key} =~ ^[A-Z0-9]{20}$ ]]; then
    exit_check 1 "param access_key is null or not meeting the requirements : -a=${access_key}"
  fi
}

#检查regin
function check_region() {
  if [[ -z ${region_id} ]]; then
    exit_check 1 "region_id is blank"
  else
    if [[ ! ${region_id} =~ ^[A-Za-z0-9._-]{1,100}$ ]]; then
      exit_check 1 "region_id is invalid"
    fi
  fi

  if [[ -z ${x_project_id} ]]; then
    exit_check 1 "x_project_id is blank"
  else
    if [[ ! ${x_project_id} =~ ^[A-Za-z0-9]{32}$ ]]; then
      exit_check 1 "x_project_id is invalid"
    fi
  fi
}

function check_mem_config() {
  memtotal=$(wmic OS get FreePhysicalMemory| sed -n '2p')
  m1=8388608
  m2=4194304
  if [[ $memtotal -ge $m1 ]]; then
    JAVA_START_HEAP="-Xms2048m"
    JAVA_MAX_HEAP="-Xmx4096m"
  elif [[ $memtotal -ge $m2 ]]; then
    JAVA_START_HEAP="-Xms1024m"
    JAVA_MAX_HEAP="-Xmx2048m"
  else
    JAVA_START_HEAP="-Xms256m"
    JAVA_MAX_HEAP="-Xmx512m"
  fi
}

# check检查git安装
function check_git() {
  GIT_PATH=$(which git)
  if [[ $? -ne 0 || -z ${GIT_PATH} ]]; then
    exit_check 1 "Git is not installed, please install Git."
  fi
}

# check检查unzip安装
function check_unzip() {
  UNZIP_PATH=$(which unzip)
  if [[ $? -ne 0 || -z ${UNZIP_PATH} ]]; then
    exit_check 1 "command [unzip] can not use in git bash, please install unzip."
  fi
}

function check_docker() {
  DOCKER_PATH=$(which docker)
  if [[ $? -ne 0 || -z ${DOCKER_PATH} ]]; then
    exit_check 1 "Docker is not installed, please install Docker."
  fi
}

# check检查jdk安装
function check_jre() {
  # check检查jdk安装
  source /etc/profile
  JAVA_PATH=$(which ${JAVACMD})
  if [[ $? -ne 0 || -z ${JAVA_PATH} ]]; then
    exit_check 1 "[info]JDK is not installed, please install JDK environment."
  fi
}

function check_process() {
  if [[ -e ${AGENT_BASEHOME}/process/${slave_name}.pid ]]; then
    pid=$(cat ${AGENT_BASEHOME}/process/${slave_name}.pid)
    ps -ef | grep ${pid}
    if [[ $? -eq 0 ]]; then
      exit_check 1 "Octopus Agent [${pid}] with the same slave name [${slave_name}] is running, please stop it first."
    fi
  fi
}

function download_save_as() {
  url=$1
  name=$2
  writeLog "[INFO] Download URL:${url}"
  if [[ -f $(which curl) ]]; then
    curl -# -o ${name} -k ${url}
    exit_check $? "Download failed!"
  else
    wget --no-check-certificate -O ${name} ${url}
    exit_check $? "Download failed!"
  fi
}

function download_and_unzip_package() {
  cd ${AGENT_BASEHOME}
  download ${OCTOPUS_AGENT_URL}  octopus-agent-${VERSION}.zip
  download ${OCTOPUS_AGENT_URL}.sha256 octopus-agent-${VERSION}.zip.sha256

  # check sha256
  sha256_1=$(cat octopus-agent-${VERSION}.zip.sha256 | awk '{print $1}')
  sha256_2=$(sha256sum octopus-agent-${VERSION}.zip | awk '{print $1}')
  if [[ "${sha256_1}" != "${sha256_2}" ]]; then
    exit_check 1 "Check octopus-agent-${VERSION}.zip sha256sum fail."
  fi
  writeLog "[INFO] Check octopus-agent-${VERSION}.zip sha256sum success!"

  unzip -o -q -u octopus-agent-${VERSION}.zip 2>> ${OCTOPUS_AGENT_LOG}
  writeLog "[INFO] Decompress octopus-agent-${VERSION}.zip package success!"
}

function ping_check() {
  # 物理局点极其匹配的逻辑局点，agent连接master使用同一个物理局点的域名
  if [[ ${region_id} == "cn-north-1" || ${region_id} == "cn-north-4" || ${region_id} == "cn-northeast-1" ]]; then
    PHYSICAL_REGION="cn-north-4"
  elif [[ ${region_id} == "cn-east-3" || ${region_id} == "cn-east-2" || ${region_id} == "cn-south-1" || ${region_id} == "cn-south-2" || ${region_id} == "cn-southwest-2" ]]; then
    PHYSICAL_REGION="cn-south-1"
  else
    return 0
  fi
  AGENT_ELB_DOMAIN=agent.codearts.${PHYSICAL_REGION}.myhuaweicloud.com

  if [[ ! -z ${AGENT_ELB_DOMAIN} ]]; then
    local rate=$(ping -c 1 -w 3 ${AGENT_ELB_DOMAIN} | grep 'packet loss' | grep -v grep | awk -F',' '{print $3}' | awk -F'%' '{print $1}' | awk '{print $NF}')
    if [[ "${rate}" == "errors" ]]; then
      rate=$(ping -c 1 -w 3 ${AGENT_ELB_DOMAIN} | grep 'packet loss' | grep -v grep | awk -F',' '{print $4}' | awk -F'%' '{print $1}' | awk '{print $NF}')
      exit_check 1 "Unable to connect ${AGENT_ELB_DOMAIN},please check your network.[ ping ${AGENT_ELB_DOMAIN} ]"
    fi
  fi
}

function check_network() {
  if [[ ${region_id} == "cn-north-1" || ${region_id} == "cn-north-4" || ${region_id} == "cn-east-3" || ${region_id} == "cn-east-2" || ${region_id} == "cn-south-1" || ${region_id} == "cn-south-2" || ${region_id} == "cn-northeast-1" || ${region_id} == "cn-southwest-2" ]]; then
    ping_check
  fi
}

function check_param_env() {
  #  环境网络监测
  check_network
  #参数校验
  #  用户校验
  if [[ ${user_check} != 'false' ]]; then
    writeLog "[INFO] check start user"
    check_user_slave
  fi
  #  检查access_key，secret_access_key
  check_ak_sk
  #  检查cluster_id
  check_cluster_id
  #  检查region_id
  check_region
  #  检查slave_name
  check_slave_name
  #  检查work_dir
  check_workdir
  #  检查endpoint
  check_endpoint
  #  检查x_auth_token
  check_token
  #  检查label
  check_label
  #  检查mem_min，mem_max
  check_mem_config
  #  检查安装unzip
  check_unzip
  #  检查jdk
  check_jre
  #  检查git
  check_git
  #  检查docker
  if [[ ${need_docker} == 'false' ]]; then
    writeLog "[INFO] not need install docker. "
  else
    check_docker
  fi
  clear_package_and_files
  #  检查进程
  check_process
}

function init_agent_url() {
  DOMAIN_NAME=${obs_domain_name}
  if [[ ${hcs} == 'true' ]]; then
    OCTOPUS_AGENT_URL=https://${DOMAIN_NAME}/${VERSION}/octopus-agent-${VERSION}_zip
    OCTOPUS_ENV_URL=https://${DOMAIN_NAME}/${VERSION}/octopus-env-init-install-${VERSION}_zip
  else
    OCTOPUS_AGENT_URL=https://${DOMAIN_NAME}/${VERSION}/octopus-agent-${VERSION}.zip
    OCTOPUS_ENV_URL=https://${DOMAIN_NAME}/${VERSION}/octopus-env-init-install-${VERSION}.zip
  fi
  # get agent latest version
  writeLog "[INFO] Agent version:${VERSION}"
}

function create_dir() {
  if [[ ! -d ${AGENT_BASEHOME} ]]; then
    mkdir -p ${AGENT_BASEHOME}
  fi

  if [[ ! -d ${JDK_INSTALL_PATH} ]]; then
    mkdir -p ${JDK_INSTALL_PATH}
  fi
  install_log_path=${AGENT_BASEHOME}/logs
  if [[ ! -d ${install_log_path} ]]; then
    mkdir -p ${AGENT_BASEHOME}/logs
    touch ${OCTOPUS_AGENT_LOG}
  fi
  if [[ ! -d ${AGENT_BASEHOME}/process ]]; then
    mkdir -p ${AGENT_BASEHOME}/process
  fi
}

function install_octopus_agent() {

  # 启动参数
  MAIN_CLASS_ARGS="${JAVA_START_HEAP} ${JAVA_MAX_HEAP} -Dagent.slave.name=${slave_name}"
  if [[ ! -z ${cluster_id} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dcluster.id=${cluster_id}"
  fi
  if [[ ! -z ${work_dir} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.work.dir=${work_dir}"
  fi
  if [[ ! -z ${label} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.label=${label}"
  fi
  if [[ ! -z ${access_key} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Daccess.key=${access_key}"
  fi
  if [[ ! -z ${secret_access_key} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dsecret.access.key=${secret_access_key}"
  fi
  if [[ ! -z ${region_id} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dregion.id=${region_id}"
  fi
  if [[ ! -z ${x_project_id} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dx.project.id=${x_project_id}"
  fi
  if [[ ! -z ${user_id} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.user.id=${user_id}"
  fi
  if [[ ! -z ${user_name} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.user.name=${user_name}"
  fi
  if [[ ! -z ${domain_id} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.domain.id=${domain_id}"
  fi
  if [[ ! -z ${domain_name} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.domain.name=${domain_name}"
  fi
  if [[ ! -z ${endpoint} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dserver.endpoint=${endpoint}"
  fi
  if [[ ! -z ${x_auth_token} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dx.auth.token=${x_auth_token}"
  fi
  if [[ ! -z ${external_global_domain_name} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dglobal.domain.name=${external_global_domain_name}"
  fi
  if [[ ! -z ${auth_secret} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dauth.secret=${auth_secret}"
  fi
  if [[ ! -z ${cluster_resource_type} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dcluster.resource.type=${cluster_resource_type}"
  fi
  if [[ ! -z ${ip} ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dip=${ip}"
  fi
  MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dagent.version=${VERSION}"
  if [[ ${register_timer} == 'true' ]]; then
    MAIN_CLASS_ARGS="${MAIN_CLASS_ARGS} -Dregister.service=true"
  fi

  cd "${AGENT_BASEHOME}/octopus-agent-${VERSION}"
  process_count=$(ps -ef | grep "${MAIN_CLASS}" | grep -v grep | wc -l)
  if [[ $process_count -eq 0 ]]; then
    true >"${AGENT_BASEHOME}/process/${slave_name}.pid"
  fi

  JVM_ARGS="-Djava.util.logging.config.file=${JAVA_LOG_CONFIG_DIR}\\\\logging.properties ${MAIN_CLASS_ARGS} $JVM_ARGS"
  sed -i "/java.util.logging.FileHandler.pattern=/d" ${CONFIG_DIR}/logging.properties
  echo "java.util.logging.FileHandler.pattern=${work_dir}/remoting/logs/remoting.log" >>${CONFIG_DIR}/logging.properties
  # rm Console logger
  sed -i '/<AppenderRef ref=\"Console\"\/>/d' "${CONFIG_DIR}/log4j2.xml"
  JVM_ARGS="-Dlog4j.configurationFile=${JAVA_LOG_CONFIG_DIR}\\\\log4j2.xml $JVM_ARGS"

  which /bin/tee >/dev/null 2>&1

  source /etc/profile
  exec nohup ${JAVACMD} -jar $JVM_ARGS ${JAR_PATH} ${MAIN_CLASS} 2>&1 &
  echo $! > ${AGENT_BASEHOME}/process/${slave_name}-tmp.pid

  if [[ ${register_timer} == 'true' ]]; then
    start_agent_script "nohup ${JAVACMD} -jar $JVM_ARGS ${JAR_PATH} ${MAIN_CLASS} 2>&1 &"
    TIMER_PATH="${work_dir}/${slave_name}Timer.bat"

    echo -e "echo Y | schtasks /create /tn "${slave_name}" /tr ${work_dir}/${slave_name}.bat /sc minute /mo 2 /ru System " >  ${TIMER_PATH}
    echo -e "echo Y | schtasks /create /tn "${slave_name}"_001 /tr ${work_dir}/${slave_name}.bat /sc onstart /ru System " >>  ${TIMER_PATH}
    echo -e "schtasks /run /tn "${slave_name}"_001" >>  ${TIMER_PATH}
    echo -e "schtasks /run /tn "${slave_name}"" >>  ${TIMER_PATH}
    stop_agent_timer_scripts
  fi
}

function start_agent_script() {
PROCESS_START_SHELL_FILE_PATH="${work_dir}/${slave_name}.sh"
cat << EOF > ${PROCESS_START_SHELL_FILE_PATH}
source /etc/profile
AGENT_BASEHOME=${AGENT_BASEHOME}
slave_name=${slave_name}
OCTOPUS_AGENT_LOG=${OCTOPUS_AGENT_LOG}
EOF
cat << 'EOF' >> ${PROCESS_START_SHELL_FILE_PATH}
function writeLog() {
  msg="$1\n"
  printf "[$(date '+%Y-%m-%d %H:%M:%S')] $msg" | tee -a ${OCTOPUS_AGENT_LOG}
}

if [[ -e ${AGENT_BASEHOME}/process/${slave_name}.pid ]]; then
  pid=$(cat ${AGENT_BASEHOME}/process/${slave_name}.pid)
  ps -ef | grep ${pid}
  if [[ $? -eq 0 ]]; then
    exit 0
  fi
fi
EOF
echo "exec $1" >> ${PROCESS_START_SHELL_FILE_PATH}
cat << 'EOF' >> ${PROCESS_START_SHELL_FILE_PATH}
new_pid=$!
sleep 20
ps -ef | grep ${new_pid}| grep java
if [[ $? -eq 0 ]]; then
  echo "${new_pid}" > "${AGENT_BASEHOME}/process/${slave_name}.pid"
  writeLog "[WARN] Octopus Agent [${new_pid}] with the slave name [${slave_name}] restart."
fi
EOF

  # add windows timer,2min
DAEMON_BASH_PATH="${work_dir}/${slave_name}.bat"
cat << EOF > ${DAEMON_BASH_PATH}
@echo off
for /f "delims=" %%a in ('cmd /c "where git"') do (
    set "gitBashPath=%%a"
    goto :found
)
:found
echo git path: %gitBashPath%
set "gitBashPath=%gitBashPath:~0,-11%"

set "gitBashPath=%gitBashPath%bin\bash.exe"
echo git bash path: %gitBashPath%
IF EXIST "%gitBashPath%" (
echo git bash path exits
start "" "%gitBashPath%" -c "${work_dir}/${slave_name}.sh"
) ELSE (
start "" "bash.exe" -c "${work_dir}/${slave_name}.sh"
)
EOF
}

function stop_agent_timer_scripts() {
  STOP_TIMER_PATH="${work_dir}/stopTimer_${slave_name}.bat"
  echo -e "schtasks /delete /tn "${slave_name}" /f" >  ${STOP_TIMER_PATH}
  echo -e "schtasks /delete /tn "${slave_name}"_001 /f" >>  ${STOP_TIMER_PATH}
}

function check_install_result() {
  writeLog "[INFO] Wait agent starting..."
  if [[ $? -eq 0 ]]; then
    sleep 20
    pid=$(cat ${AGENT_BASEHOME}/process/${slave_name}-tmp.pid)
    ps -ef | grep ${pid}| grep java
    if [[ $? -ne 0 ]]; then
      if [[ -e "${OCTOPUS_AGENT_LOG}" ]]; then
        tail -100 ${OCTOPUS_AGENT_LOG} >${AGENT_TEMP_LOG}
        error_num=$(grep -n "ERROR" ${AGENT_TEMP_LOG} | tail -1 | awk -F":" '{print $1}')
        if [[ -n "${error_num}" ]]; then
          tail -n +${error_num} ${AGENT_TEMP_LOG}
        fi
        rm -f ${AGENT_TEMP_LOG}
      fi
      writeLog "[ERROR]Failed to start agent, cloud not find Agent process id, please check ${OCTOPUS_AGENT_LOG} ,error message in console for detail information.\n"
      rm -rf ${AGENT_BASEHOME}/process/${slave_name}-tmp.pid
      exit 1
    fi
    echo "${pid}" > "${AGENT_BASEHOME}/process/${slave_name}.pid"
    writeLog "[INFO] Success to start Octopus Agent [${pid}]."
    if [[ ${register_timer} == 'true' ]]; then
      winpty ${work_dir}/${slave_name}Timer.bat
    fi
  else
    writeLog "[ERROR] Failed to start Octopus Agent."
  fi
  rm -rf ${AGENT_BASEHOME}/process/${slave_name}-tmp.pid
}
#/opt/octopus-agent
function clear_package_and_files() {
  rm -rf ${AGENT_BASEHOME}/octopus-agent-${VERSION}.*
  rm -rf ${AGENT_BASEHOME}/octopus-env*
}

#主函数
function main() {
  create_dir
  writeLog "[INFO] 1.Prepare to install agent success!"
  #生成下载url
  if [[ -z "${obs_domain_name}" ]]; then
    init_agent_url
  else
    writeLog "[INFO] Download Agent URL ${OCTOPUS_AGENT_URL}!"
  fi
  #参数和环境校验
  check_param_env
  writeLog "[INFO] 2.Check param and env success!"
  # agent has been installed
  clear_package_and_files
  writeLog "[INFO] 3.Clear directory success!"
  #下载agent
  download_and_unzip_package
  writeLog "[INFO] 4.Download and decompress packages success!"
  # 安装agent
  install_octopus_agent
  check_install_result
  writeLog "[INFO] 5.Install Octopus Agent success!"
  clear_package_and_files
  writeLog "[INFO] Agent output logs will be printed to [ ${work_dir}/remoting/logs/remoting.log ]"
  writeLog "[INFO] End Install Octopus Agent,Agent output logs have been printed to [ ${OCTOPUS_AGENT_LOG} ]"
}

work_dir_path=${work_dir}
OLD_IFS="$IFS"
IFS=":"
read -ra path_arr <<< "${work_dir_path}"
IFS="$OLD_IFS"
root_drive_path=${path_arr[0]}":"

VERSION=latest
if [[ -z ${agent_version} ]]; then
  writeLog "[INFO]agent version is latest"
else
  VERSION=${agent_version}
  writeLog "[INFO]agent version is ${agent_version}"
fi
if [[ -z ${register_timer} ]]; then
  register_timer="false"
fi
#参数初始化
JAVA_START_HEAP="Xms256m"
JAVA_MAX_HEAP="Xmx512m"
JAVACMD="java"
MAIN_CLASS="com.huawei.devcloud.agent.AgentStart"

#基础环境安装相关参数
if [[ ${hcs} == 'true' ]]; then
  OCTOPUS_ENV_URL=https://${obs_domain_name}/${VERSION}/octopus-env-init-install-${VERSION}_zip
  OCTOPUS_AGENT_URL=https://${obs_domain_name}/${VERSION}/octopus-agent-${VERSION}_zip
else
  OCTOPUS_ENV_URL=https://${obs_domain_name}/${VERSION}/octopus-env-init-install-${VERSION}.zip
  OCTOPUS_AGENT_URL=https://${obs_domain_name}/${VERSION}/octopus-agent-${VERSION}.zip
fi
CURRENT_ENV_INIT_HOME=${root_drive_path}/opt/octopus-agent/octopus-env-init-install-${VERSION}
JDK_INSTALL_PATH=/usr/local

#安装目录
AGENT_BASEHOME=${root_drive_path}/opt/octopus-agent
DECRYPT_PATH=com.huawei.devcloud.agent.util.Decrypt
LIB_DIR=${root_drive_path}/opt/octopus-agent/octopus-agent-${VERSION}/lib
JAR_PATH=${root_drive_path}/opt/octopus-agent/octopus-agent-${VERSION}/octopus-agent.jar
CONFIG_DIR=${root_drive_path}/opt/octopus-agent/octopus-agent-${VERSION}/conf
JAVA_LOG_CONFIG_DIR=${root_drive_path}"\\\\opt\\\\octopus-agent\\\\octopus-agent-"${VERSION}"\\\\conf"
#日志路径
OCTOPUS_AGENT_LOG=${root_drive_path}/opt/octopus-agent/logs/octopus-agent.log
AGENT_TEMP_LOG=${root_drive_path}/opt/octopus-agent/logs/agent-temp.log

main
