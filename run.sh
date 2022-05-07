#!/bin/sh

if [ $1 ];then 
    sleep $1
fi

docker start redis


tmux kill-pane -t nb2 
#tmux kill-pane -t G

tmux new -s nb2 -d 
#tmux new -s G  -d 

#tmux send -t "nb2" "export ALL_PROXY=http://127.0.0.1:1081;cd /root/my_nonebot2/nb2/; python bot.py >> ../nb2.log" Enter
#tmux send -t "gocq" "cd /root/my_nonebot2/gocq/ ; ./go >> ../goqp.log" Enter

tmux send -t "nb2" "cd ;cd my_nonebot2/; nb run > /root/nb2.log" Enter
#tmux send -t "G" "/root/env/bin/rclone mount G:/ /root/GoogleTeam --cache-dir /root/Downloads/temp" Enter
#tmux send -t "gocq" "cd ;cd my_nonebot2/gocq/ ; ./go" Enter
