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

import os,sys

from processing.core.GeoAlgorithm import GeoAlgorithm
from processing.core.parameters import *
from processing.core.outputs import OutputVector
from processing.tools import dataobjects, vector, system
from qgis.core import QgsVectorFileWriter

try:
    from Queue import Queue, Empty
except ImportError:
    from queue import Queue, Empty  
from subprocess import PIPE, Popen, STDOUT

sdna_to_qgis_vectortype = {"Polyline":ParameterVector.VECTOR_TYPE_LINE}
sdna_to_qgis_fieldtype = {"Numeric":ParameterTableField.DATA_TYPE_NUMBER}

def enqueue_output(command, queue):
    queue.put(command+"\n\n")
    ON_POSIX = 'posix' in sys.builtin_module_names
    p = Popen(command+" 2>&1", shell=True, stdout=PIPE, bufsize=0, close_fds=ON_POSIX)
    while True:
        data = p.stdout.read(1)
        if not data:
            break
        queue.put(data)
    p.stdout.close()
    p.wait()
    queue.put("\nDone.\n")

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
        
        #working dummy copy:
        src=syntax["inputs"].values()[0]
        dst=syntax["outputs"].values()[0]
        command = "copy "+src+" "+dst
        progress.setInfo(command)
        from shutil import copyfile
        copyfile(src,dst)
        copyfile(src[:-3]+"shx",dst[:-3]+"shx")
        copyfile(src[:-3]+"dbf",dst[:-3]+"dbf")
        #enqueue_output(command,Queue())
        # run comand in process (change syntax so command is literal command line command)
        
        
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
        sys.path.insert(0,sdnapath+os.sep+"..")
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
