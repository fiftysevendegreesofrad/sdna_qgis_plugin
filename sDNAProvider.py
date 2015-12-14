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

import os,sys,time,re

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
        
    def help(self):
        return True, self.sdnatool.desc
        
    def defineCharacteristics(self):
        # The name that the user will see in the toolbox
        self.name = self.sdnatool.alias

        # The branch of the toolbox under which the algorithm will appear
        self.group = 'Spatial Design Network Analysis'

        self.varnames = []
        self.outputnames = []
        self.selectvaroptions = {}
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
                    self.selectvaroptions[varname] = filter
                else:
                    self.addParameter(ParameterString(varname,self.tr(displayname),default,False,not required))
            else:
                assert False # unrecognized parameter type
                

    def processAlgorithm(self, progress):
        
        if ProcessingConfig.getSetting(ProcessingConfig.USE_SELECTED):
            progress.setInfo("**********************************************************************\n"\
                             "WARNING: sDNA ignores your selection and will process the entire layer\n"\
                             "**********************************************************************")
        
        args = {}
        for outname,output in zip(self.outputnames,self.outputs):
            args[outname]=output.getCompatibleFileName(self)
        for vn in self.varnames:
            args[vn]=self.getParameterValue(vn)
            if vn in self.selectvaroptions:
                args[vn] = self.selectvaroptions[vn][args[vn]]
            if args[vn]==None:
                args[vn]=""
        args["arcxytol"]=""
        args["arcztol"]=""
        
        syntax = self.sdnatool.getSyntax(args)
        
        # convert inputs to shapefiles if necessary, renaming in syntax as appropriate
        converted_inputs={}
        for name,path in syntax["inputs"].iteritems():
            if path:
                # convert inputs to shapefiles if they aren't already
                # do this by hand rather than using dataobjects.exportVectorLayer(processing.getObject(path))
                # as we want to ignore selection if present
                if path[-4:].lower()!=".shp":
                    progress.setInfo("Converting input to shapefile: "+path)
                    tempfile = system.getTempFilename("shp")
                    ret = QgsVectorFileWriter.writeAsVectorFormat(processing.getObject(path), tempfile, "utf-8", None, "ESRI Shapefile")
                    assert(ret == QgsVectorFileWriter.NoError)
                    converted_inputs[name]=tempfile
                else:
                    converted_inputs[name]=path
        syntax["inputs"]=converted_inputs
               
        def map_to_string(map):
            return '"'+";".join((k+"="+v for k,v in map.iteritems()))+'"'
        
        # run command in subprocess, copy stdout/stderr back to qgis dialog
        command = self.provider.sdnapath+os.sep+syntax["command"]+".py --im "+map_to_string(syntax["inputs"])+" --om "+map_to_string(syntax["outputs"])+" "+syntax["config"]
        progress.setInfo("Running external command: "+command)
        
        # because select.select doesn't work on Windows,
        # create threads to call blocking stdout/stderr pipes
        # and update progress when polled
        class ForwardPipeToProgress:
            def __init__(self,progress,prefix,pipe,outputblank):
                self.outputblanklines=outputblank
                self.unfinishedline=""
                self.prefix=prefix
                self.progress=progress
                self.q = Queue()
                self.regex = re.compile("Progress: ([0-9][0-9]*.?[0-9]*)%")
                self.seen_cr = False
                def enqueue_output(out, queue):
                    while True:
                        data = out.read(1) # blocks
                        if not data:
                            break
                        else:
                            queue.put(data)
                t = Thread(target=enqueue_output, args=(pipe, self.q))
                t.daemon = True # thread won't outlive process
                t.start()
            
            def _output(self):
                m = self.regex.match(self.unfinishedline.strip())
                if m:
                    self.progress.setPercentage(float(m.group(1)))
                else:
                    if self.outputblanklines or self.unfinishedline!="":
                        self.progress.setInfo(self.prefix+self.unfinishedline)
                self.unfinishedline=""
            
            def poll(self):
                while not self.q.empty():
                    char = self.q.get_nowait()
                    # treat \r and \r\n as \n
                    if char == "\r":
                        self.seen_cr = True
                        continue
                    if char == "\n" or self.seen_cr:
                        self._output()
                        if char != "\n":
                            self.unfinishedline+=char
                        self.seen_cr = False
                    else:
                        self.unfinishedline+=char
            
            def flush(self):
                self.poll()
                if self.unfinishedline:
                    self._output()
        
        # fork subprocess
        ON_POSIX = 'posix' in sys.builtin_module_names
        environ = os.environ.copy()
        environ["PYTHONUNBUFFERED"]="1" # equivalent to calling with "python -u"
        del environ["PYTHONHOME"] # ensure we use system python ctypes not QGIS's embedded python ctypes
        # MUST create pipes for stdin, out and err because http://bugs.python.org/issue3905
        p = Popen(command, shell=True, stdin=PIPE, stdout=PIPE, stderr=PIPE, bufsize=0, close_fds=ON_POSIX, env=environ)
        fout = ForwardPipeToProgress(progress,"",p.stdout,True)
        ferr = ForwardPipeToProgress(progress,"ERR: ",p.stderr,False)
        
        while p.poll() is None:
            fout.poll()
            ferr.poll()
            time.sleep(0.3)
            
        p.stdout.close()
        p.stderr.close()
        p.stdin.close()
        fout.flush()
        ferr.flush()
        p.wait()
        progress.setInfo("External command completed")
        
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
                
        self.sdnapath = sdnapath
        
        # import sDNAUISpec
        sdnarootdir = sdnapath+os.sep+".."
        if not sdnarootdir in sys.path:
            sys.path.insert(0,sdnarootdir) # actualy python path not system path
        try:
            import sDNAUISpec
            reload(sDNAUISpec)
        except ImportError:
            self.installsdna()
            return
        
        # load tools
        self.alglist = []
        for toolclass in sDNAUISpec.get_tools():
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
