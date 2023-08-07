# nonebot_plugin_masterduel
大师决斗卡查等各种功能的一个Nonebot2机器人插件

1.ygo 闪刀                                 
描述: 模糊查询闪刀字样的卡

2.ygogpt 名字带有闪刀的炎属性怪兽  

描述: 通过gpt查询游戏王卡

例子1: 名字带有刀的同调怪兽

例子2: 一只又是超量又是灵摆的暗属性龙族怪兽

例子3: 等级等于8的龙族怪兽

例子4: 名字中带有闪刀的永续魔法 



3.ygoalias id 火刀            
描述: 卡片别名设置, id来自查询卡的名字后的那个id,然后查询就先匹配别名

4.ygos attribute=暗属性|type=怪兽卡|monsters_type=超量|monsters_type=灵摆    

描述: 精确查找
1. attribute = 是怪兽时: 火属性,水属性,光属性, 非怪兽时: 场地魔法, 永续魔法, 速攻魔法, 反击陷阱, 永续陷阱

- type = 怪兽卡, 陷阱卡, 魔法卡
- monsters_type = 超量, 融合, LINK, 灵摆, 通常, 二重, 灵魂, 同调

- race = 龙族, 战士族

- ATK = 输入数字

- DEF = 输入数字

- lv = 输入数字

- q = 模糊搜索条件

例子一: lv=1|type=怪兽卡|attribute=炎属性|type=怪兽卡|q=闪刀

查询结果:  火刀
