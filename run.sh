#!/bin/sh

if [ $1 ];then 
    sleep $1
fi

docker start redis


tmux kill-pane -t nb2 
#tmux kill-pane -t gocq

tmux new -s nb2 -d 
#tmux new -s gocq  -d 

#tmux send -t "nb2" "export ALL_PROXY=http://127.0.0.1:1081;cd /root/my_nonebot2/nb2/; python bot.py >> ../nb2.log" Enter
#tmux send -t "gocq" "cd /root/my_nonebot2/gocq/ ; ./go >> ../goqp.log" Enter

tmux send -t "nb2" "cd ;cd my_nonebot2/; python bot.py > /root/nb2.log" Enter
#tmux send -t "gocq" "cd ;cd my_nonebot2/gocq/ ; ./go" Enter
