#!/bin/bash 
firewall-cmd --zone=public --add-port=$1/tcp --permanent
firewall-cmd --reload
firewall-cmd --zone=public --list-ports
