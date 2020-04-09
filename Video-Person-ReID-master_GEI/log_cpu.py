#-*- coding: utf-8 -*-
####print the usage of CPU and utilization of CPU, save the content to the log

import logging
import psutil
import os

import sys
print(sys.getdefaultencoding())
def lxy_log_cpu():
    #creat log_out file
    log_filename="logging.txt"

    #set file format
    log_format ="%(asctime)s %(message)s"

    #log file basic config
    logging.basicConfig(format=log_format,datefmt="%Y-%m-%d %H:%M:%S %p",level=logging.DEBUG,filename=log_filename,filemode="w")


    logging.debug("cpu usage###############")

    #获取当前运行的pid
    p1=psutil.Process(os.getpid())

    #打印本机的内存信息
    #print(psutil.virtual_memory)
    #print ('直接打印内存占用： '+(str)(psutil.virtual_memory))
    #logging.debug("memory orcupy:"+(str)(psutil.virtual_memory))



    #打印内存的占用率
    print ('获取内存占用率:'+(str)(psutil.virtual_memory().percent)+'%')
    logging.debug('memort usage:'+(str)(psutil.virtual_memory().percent)+'%')
    #本机cpu的总占用率
    print ('打印本机cpu占用率:'+(str)(psutil.cpu_percent(0))+'%')
    logging.debug('Total cpu usage:'+(str)(psutil.cpu_percent(0))+'%')

    #该进程所占cpu的使用率
    print ("打印该进程CPU占用率:"+(str)(p1.cpu_percent(None))+"%")
    logging.debug("This thread cpu usage:"+(str)(p1.cpu_percent(None))+"%")

    #直接打印进程所占内存占用率
    print (p1.memory_percent)
    logging.debug(p1.memory_percent)

    #格式化后显示的进程内存占用率
    print ("percent: %.2f%%" % (p1.memory_percent()))
    logging.debug("percent: %.2f%%" % (p1.memory_percent()))

    p1 = psutil.Process(os.getpid())
    print("the pid cpu usage:" + (str)(p1.cpu_percent(None)) + "%")
    print("memory usage:"+(str)(psutil.virtual_memory().percent) + '%')
    print("cpu usage:"+(str)(psutil.cpu_percent(0)) + '%')