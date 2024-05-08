#!/usr/bin/env python
# -*- coding: utf-8 -*-

import math
import numpy as np
from scipy.stats import norm

import sys
import os
import threading
import datetime


from logging import error
import signal
import re

import serial

import tkinter as tk
from tkinter import ttk

import pylab as plt
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg


BAUDRATE=9600
UNO_COMPORT='COM3'
PRINT_CONSOLE_LOG=True
PLOT_UPDATE_EACH_ms=3000
MAX_ARRAY_LIMIT_BEFORE_SHIFTING=3600 # for each second it gives 1 hour
START_TIME = datetime.datetime.now()
CSV_FILE_NAME='pressure_Start{}.csv'.format(START_TIME.strftime("%d_%m_%H-%M-%S"))

def log(msg):
    if PRINT_CONSOLE_LOG:
        print(msg)

# ------------------ Intercept CTRL+C -----------------------
g_Close = False
def signal_handler(sig, frame):
    global g_Close
    global g_CSVFile
    print('Stopping!')
    g_Close = True
    serialPort.close()
    g_CSVFile.close()
    sys.exit(0)

# ----------------- Reading Pressure and Arduino stuff------
g_TimeLine, g_Pressures, g_TickCounter = [], [], 0


def extract_pressure(message):
    m = re.search('Pressure:\s*(\d+?)Pa$', message)
    return int(m.group(1)) if m else None


def to_mm_m_c(pressure_pa):
    return pressure_pa / 133.3224


def add_pressure(pressure_pa):
    global g_Pressures
    global g_TimeLine
    global g_TickCounter
    g_TickCounter += 1

    g_CSVFile.write(f"{g_TickCounter};{pressure_pa}\n")
    if g_TickCounter % 10:
        g_CSVFile.flush()
        
    if len(g_Pressures) < MAX_ARRAY_LIMIT_BEFORE_SHIFTING:
        g_Pressures = np.append( g_Pressures, [pressure_pa])
        g_TimeLine = np.append( g_TimeLine, [g_TickCounter] )
    else:
        g_Pressures = np.roll(g_Pressures, -1)
        g_TimeLine   = np.roll(g_TimeLine,   -1)
        g_Pressures[-1] = pressure_pa
        g_TimeLine[-1]   = g_TickCounter

    log("Pressure> {} pa ({} mm of mercury column)".format(pressure_pa, to_mm_m_c(pressure_pa)))


def arduino_pressure_reader(serialPort):
    global g_Close
    while serialPort.isOpen():
        if g_Close:
            break

        lineBytes = serialPort.readline()
        line = lineBytes.decode("utf-8").strip()

        if line.startswith('Pressure:'):
            add_pressure( extract_pressure(line) )
        else:
            log("> {}".format(line))


def calc_gaussian(values):
    mu    = np.mean(values)
    sigma = np.std(values)
    p1, p2 =  np.min(values), np.max(values)

    z1 = ( p1 - mu ) / sigma
    z2 = ( p2 - mu ) / sigma
    x = np.arange(z1, z2, 0.001) # range of x in spec
    x_all = np.arange(-10, 10, 0.001) # entire range of x, both in and out of spec
    # mean = 0, stddev = 1, since Z-transform was calculated
    y  = norm.pdf(x, 0, 1)
    y2 = norm.pdf(x_all, 0, 1)
    return x, x_all, y, y2


class PlotFigure:
    def __init__(self, title, root):
        self.top = tk.Toplevel(root)
        self.top.title("Win: " + title)
        self.top.geometry('500x400')
        self.fig = plt.Figure(figsize=(5,5), dpi=100)
        plt.style.use('fivethirtyeight')
        self.canvas = FigureCanvasTkAgg(self.fig, self.top)
        self.canvas.get_tk_widget().pack(side=tk.BOTTOM, fill=tk.BOTH, expand=True)

        self.left_y_plot = self.fig.add_subplot(111)
        self.left_y_plot.set_xlabel('Time (s)')
        self.left_y_plot.set_ylabel('Pressure (Pa)', color="#5b2c6f")


    def setgeometry(self, pos=(0,0), size=(500,600)):
        w,h = size
        x,y = pos
        self.top.geometry('%dx%d+%d+%d' % (w, h, x, y))


    def plot(self, xvals, yvals):
        self.left_y_plot.cla()
        self.left_y_plot.set_xlabel('Time (s)')
        self.left_y_plot.set_ylabel('Pressure (Pa)', color="#5b2c6f")
        self.left_y_plot.plot(xvals, yvals, label='DATA', color='#2471a3', lw=3, linestyle='dashdot')

        self.canvas.draw()


    def plot_gauss(self, x, x_all, y, y2):
        self.left_y_plot.cla()

        self.left_y_plot.fill_between(x,y,0, alpha=0.3, color='b')
        self.left_y_plot.fill_between(x_all,y2,0, alpha=0.1)
        self.left_y_plot.set_xlim([-4,4])
        self.left_y_plot.set_xlabel('# of Standard Deviations Outside the Mean')
        # self.left_y_plot.set_yticklabels([])
        self.left_y_plot.set_title('Normal Gaussian')

        self.left_y_plot.plot(x_all,y2, color='#e74c3c')

        self.fig.tight_layout()
        self.canvas.draw()

    def plot_histogram(self, vals):
        self.left_y_plot.cla()

        counts, bins = np.histogram(vals)
        self.left_y_plot.stairs(counts, bins, edgecolor='k', fill=True)
        self.canvas.draw()


class MyApp:

    def __init__(self):
        self.top = tk.Tk()
        self.top.title("Plotting Program")
        self.top.geometry('100x50')

        screen_width  = self.top.winfo_screenwidth()
        screen_height = self.top.winfo_screenheight()

        #self.__scan_mutex = threading.Lock()
        self.timeLine = PlotFigure(title="Sensor Plotter", root= self.top)
        self.gaussian = PlotFigure(title="Normal Gaussian Curve", root= self.top)
        self.histogram= PlotFigure(title="Histogram", root= self.top)

        self.timeLine.setgeometry( pos=(int(screen_width * 0.1),100), size=(int(screen_width/2) - 100, int(screen_height * 0.8)) )
        self.gaussian.setgeometry( pos=(int(screen_width/2)+50, 100), size=(int(screen_width/2) - 100, int(screen_height * 0.8)) )


    def __run_timer(self):
        self.top.after(PLOT_UPDATE_EACH_ms, self.__update_plot)


    def __update_plot(self):
        self.__plotPressureTimeLine()
        self.top.after(PLOT_UPDATE_EACH_ms, self.__update_plot)


    def Run(self):
        self.__run_timer()
        self.top.mainloop()


    def __plotPressureTimeLine(self):
        self.timeLine.plot(g_TimeLine,g_Pressures)

        x, x_all, y1, y2 = calc_gaussian(g_Pressures)

        self.gaussian.plot_gauss(x, x_all, y1, y2)

        self.histogram.plot_histogram(g_Pressures)


# ------------ program ----------------------
# ** Setup and OPEN COM port
serialPort = serial.Serial(
    port=UNO_COMPORT,
    baudrate= BAUDRATE,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=20
)

log('Start {} bauds for {}'.format(BAUDRATE, UNO_COMPORT))
if not serialPort.isOpen():
    log('Failed to open SerialPort {}'.format(UNO_COMPORT))
    sys.exit(666)

# ** Open CSV file *****
g_CSVFile=open(CSV_FILE_NAME, "a")
g_CSVFile.write("Tick;Pressure\n")

# ** SETUP CTRL+C signal handler
signal.signal(signal.SIGINT, signal_handler)

scan_thread = threading.Thread(target=lambda: arduino_pressure_reader(serialPort))
scan_thread.start()

# ** run App instance
app = MyApp()
app.Run()

# ** Cleanup
g_Close = True
if not serialPort.closed:
    serialPort.close()

if not g_CSVFile.closed:
    g_CSVFile.close()
