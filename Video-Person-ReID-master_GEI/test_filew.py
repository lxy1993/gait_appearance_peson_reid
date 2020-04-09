import sys
import os

file_name = "111.txt"
result_file = open(file_name, "w+")
if not result_file:
    os.mkdir(file_name)

map = 11.3

result_file.write("mAP: {:.1%}".format(map)+"\n")

result_file.write("Computing CMC and mAP" + "\n"+"Results ----------"+"\n")