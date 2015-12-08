# -*- coding: utf-8 -*-

"""
***************************************************************************
    Date                 : November 2015
    Copyright            : (C) 2015 by Cardiff University
                           Based on an example (C) 2013 by Victor Olaya
***************************************************************************
*                                                                         *
*   This program is free software; you can redistribute it and/or modify  *
*   it under the terms of the GNU General Public License as published by  *
*   the Free Software Foundation; either version 2 of the License, or     *
*   (at your option) any later version.                                   *
*                                                                         *
***************************************************************************
"""

__author__ = 'Crispin Cooper'
__date__ = 'November 2015'
__copyright__ = '(C) 2015, Cardiff University'

# This will get replaced with a git SHA1 when you do a git archive

__revision__ = '$Format:%H$'

from PyQt4.QtGui import *
import processing
from processing.core.AlgorithmProvider import AlgorithmProvider
from processing.core.ProcessingConfig import Setting, ProcessingConfig

import os,sys,time

from processing.core.GeoAlgorithm import GeoAlgorithm
from processing.core.parameters import *
from processing.core.outputs import OutputVector
from processing.tools import dataobjects, vector, system
from qgis.core import QgsVectorFileWriter

from subprocess import PIPE, Popen, STDOUT
try:
    from Queue import Queue, Empty
except ImportError:
    from queue import Queue, Empty
from threading import Thread

sdna_to_qgis_vectortype = {"Polyline":ParameterVector.VECTOR_TYPE_LINE}
sdna_to_qgis_fieldtype = {"Numeric":ParameterTableField.DATA_TYPE_NUMBER}

class SDNAAlgorithm(GeoAlgorithm):
    
    def __init__(self,sdnatool):
        self.sdnatool = sdnatool
        GeoAlgorithm.__init__(self)
        
    def defineCharacteristics(self):
        # The name that the user will see in the toolbox
        self.name = self.sdnatool.alias

        # The branch of the toolbox under which the algorithm will appear
        self.group = 'Spatial Design Network Analysis'

        self.varnames = []
        self.outputnames = []
        for varname,displayname,datatype,filter,default,required in self.sdnatool.getInputSpec():
            if datatype=="OFC":
                self.outputnames+=[varname]
            else:
                self.varnames+=[varname]
                
            if datatype=="FC":
                self.addParameter(ParameterVector(varname,self.tr(displayname),sdna_to_qgis_vectortype[filter],not required))
            elif datatype=="OFC":
                self.addOutput(OutputVector(varname,self.tr(displayname)))
            elif datatype=="Field":
                fieldtype,source = filter
                self.addParameter(ParameterTableField(varname,self.tr(displayname),source,sdna_to_qgis_fieldtype[fieldtype],not required))
            elif datatype=="Bool":
                self.addParameter(ParameterBoolean(varname,self.tr(displayname),default))
            elif datatype=="Text":
                if filter:
                    self.addParameter(ParameterSelection(varname,self.tr(displayname),filter))
                else:
                    self.addParameter(ParameterString(varname,self.tr(displayname),default,False,not required))
            else:
                assert False # unrecognized parameter type
                

    def processAlgorithm(self, progress):
        args = {}
        for outname,output in zip(self.outputnames,self.outputs):
            args[outname]=output.getCompatibleFileName(self)
        for vn in self.varnames:
            args[vn]=self.getParameterValue(vn)
        args["arcxytol"]=""
        args["arcztol"]=""
        
        syntax = self.sdnatool.getSyntax(args)
        
        # convert inputs to shapefiles if necessary, renaming in syntax as appropriate
        converted_inputs={}
        for name,path in syntax["inputs"].iteritems():
            if path:
                tempfile = dataobjects.exportVectorLayer(processing.getObject(path))
                progress.setInfo("exported "+path+" to "+tempfile)
                converted_inputs[name]=tempfile
        syntax["inputs"]=converted_inputs
               
        progress.setInfo(syntax.__repr__())    
        
        # run command in subprocess, copy stdout/stderr back to qgis dialog
        src=syntax["inputs"].values()[0]
        dst=syntax["outputs"].values()[0]
        command = "copy "+src+" "+dst
        progress.setInfo("Running external command: "+command)
        ON_POSIX = 'posix' in sys.builtin_module_names
        err_q = Queue()
        out_q = Queue()
        # MUST create pipes for stdin, out and err because http://bugs.python.org/issue3905
        # also create threads to handle output as select.select doesn't work with pipes on windows
        p = Popen(command+" 2>&1", shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=0, close_fds=ON_POSIX)
        
        def enqueue_output(out, queue):
            while True:
                data = out.read(1) # blocks
                if not data:
                    break
                else:
                    queue.put(data)
        
        def forward_pipe_to_queue(p,q):
            t = Thread(target=enqueue_output, args=(p, q))
            t.daemon = True # thread dies with the program
            t.start()
            
        class ForwardQueueToProgress:
            def __init__(self,progress,prefix,queue):
                self.unfinishedline=""
                self.prefix=prefix
                self.progress=progress
                self.q = queue
            def poll(self):
                while not self.q.empty():
                    char = self.q.get_nowait()
                    if char == "\n":
                        progress.setInfo(self.prefix+self.unfinishedline)
                        self.unfinishedline=""
                    else:
                        self.unfinishedline+=char

        forward_pipe_to_queue(p.stdout,out_q)
        forward_pipe_to_queue(p.stderr,err_q)
        fqpout = ForwardQueueToProgress(progress,"OUT: ",out_q)
        fqperr = ForwardQueueToProgress(progress,"ERR: ",err_q)
        
        while p.poll() is None:
            fqpout.poll()
            fqperr.poll()
            time.sleep(0.3)
            
        p.stdout.close()
        p.stderr.close()
        p.stdin.close()
        fqpout.poll()
        fqperr.poll()
        p.wait()
        progress.setInfo("External command completed")

        # run comand in process (change syntax so command is literal command line command)
        # should make shapefile environment copy projection too
        
        
class sDNAProvider(AlgorithmProvider):

    def installsdna(self):
        QMessageBox.critical(QDialog(),"sDNA: Error","Please install sDNA version 3.0 or later (http://www.cardiff.ac.uk/sdna/) then restart QGIS.")

    def getSupportedOutputVectorLayerExtensions(self):
        return ["shp"]
        
    def __init__(self):
        AlgorithmProvider.__init__(self)

        # activate provider by default
        self.activate = True
        
        # find sDNA
        matchstring = os.sep+"sDNA"+os.sep+"bin"
        matches = [path for path in os.environ["PATH"].split(os.pathsep)
                   if path[-len(matchstring):]==matchstring]

        if len(matches)==0:
            altpath = "d:\\sdna\\arcscripts\\bin"
            if os.path.exists(altpath):
                sdnapath = altpath
            else:
                self.installsdna()
                return
        else:
            sdnapath = matches[0]
            if len(matches)>1:
                QMessageBox.critical(QDialog(),"sDNA: Warning","Multiple sDNA installations found.  Using "+sdnapath)
        sdnarootdir = sdnapath+os.sep+".."
        if not sdnarootdir in sys.path:
            sys.path.insert(0,sdnarootdir)
        try:
            from sDNAUISpec import get_tools
        except ImportError:
            self.installsdna()
            return
        
        # load tools
        self.alglist = []
        for toolclass in get_tools():
            qgistool = SDNAAlgorithm(toolclass())
            qgistool.provider = self
            self.alglist += [qgistool]

    def initializeSettings(self):
        AlgorithmProvider.initializeSettings(self)
        
    def unload(self):
        AlgorithmProvider.unload(self)
        
    def getName(self):
        return 'sDNA'

    def getDescription(self):
        return 'Spatial Design Network Analysis'

    def getIcon(self):
        return AlgorithmProvider.getIcon(self)

    def _loadAlgorithms(self):
        self.algs = self.alglist
