#!/bin/bash 


os_check() { #检查系统
    if [ -e /etc/redhat-release ] ; then
        REDHAT=`cat /etc/redhat-release | cut -d' '  -f1 `
    else
        DEBIAN=`cat /etc/issue | cut -d' '  -f1 `
    fi

    if [ "$REDHAT" == "CentOS" -o "$REDHAT" == "RED" ] ; then 
        P_M=yum
    elif [ "$DEBIAN" == "Ubuntu" -o "$DEBIAN" == "ubuntu" ] ; then 
        P_M=apt-get
    else
        Operating system does not support
        exit 1
    fi
	echo 工具是 "$P_M"
}
os_check

if [ "yum" -eq $P_M ];
do
    $P_M install -y fontconfig mkfontscale
else
    $P_M install -y fontconfig xfonts-utils
done


cp simsun.ttc /usr/share/fonts/
cd /usr/share/fonts
mkfontscale
mkfontdir
fc-cache
