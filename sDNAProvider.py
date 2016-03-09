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
from processing.core.outputs import OutputVector,OutputFile
from processing.tools import dataobjects, vector, system
from qgis.core import QgsVectorFileWriter, QgsMessageLog

sdna_to_qgis_vectortype = {"Polyline":ParameterVector.VECTOR_TYPE_LINE,None:ParameterVector.VECTOR_TYPE_ANY}
sdna_to_qgis_fieldtype = {"Numeric":ParameterTableField.DATA_TYPE_NUMBER}

class SDNAAlgorithm(GeoAlgorithm):
    
    def __init__(self,sdnatool,provider):
        self.sdnatool = sdnatool
        self.provider = provider
        GeoAlgorithm.__init__(self)
        
    def help(self):
        return True, "<h2>%s</h2>%s"%(self.sdnatool.alias,self.sdnatool.desc)
        
    def defineCharacteristics(self):
        # The name that the user will see in the toolbox
        self.name = self.sdnatool.alias

        # The branch of the toolbox under which the algorithm will appear
        self.group = self.sdnatool.category

        self.varnames = []
        self.outputnames = []
        self.selectvaroptions = {}
        for varname,displayname,datatype,filter,default,required in self.sdnatool.getInputSpec():
            if datatype=="OFC" or datatype=="OutFile":
                self.outputnames+=[varname]
            else:
                self.varnames+=[varname]
                
            if datatype=="FC":
                self.addParameter(ParameterVector(varname,self.tr(displayname),sdna_to_qgis_vectortype[filter],not required))
            elif datatype=="OFC":
                self.addOutput(OutputVector(varname,self.tr(displayname)))
            elif datatype=="InFile":
                self.addParameter(ParameterFile(varname, self.tr(displayname), False, not required, filter))
            elif datatype=="OutFile":
                self.addOutput(OutputFile(varname,self.tr(displayname),filter))
            elif datatype=="Field":
                fieldtype,source = filter
                self.addParameter(ParameterTableField(varname,self.tr(displayname),source,sdna_to_qgis_fieldtype[fieldtype],not required))
            elif datatype=="MultiField":
                self.addParameter(ParameterString(varname,self.tr(displayname+" (field names separated by commas)"),default,False,not required))
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
            if hasattr(output,"getCompatibleFileName"):
                args[outname]=output.getCompatibleFileName(self)
            elif hasattr(output,"getValueAsCommandLineParameter"):
                args[outname]=output.getValueAsCommandLineParameter().replace('"','') # strip quotes - sdna adds them again
            else:
                assert False # don't know what to do with this output type
        for vn in self.varnames:
            args[vn]=self.getParameterValue(vn)
            if vn in self.selectvaroptions:
                args[vn] = self.selectvaroptions[vn][args[vn]]
            if args[vn]==None:
                args[vn]=""
        
        syntax = self.sdnatool.getSyntax(args)
        
        # convert inputs to shapefiles if necessary, renaming in syntax as appropriate
        converted_inputs={}
        for name,path in syntax["inputs"].iteritems():
            if path:
                # convert inputs to shapefiles if they aren't already shp or csv
                # do this by hand rather than using dataobjects.exportVectorLayer(processing.getObject(path))
                # as we want to ignore selection if present
                if path[-4:].lower() not in [".shp",".csv"]:
                    progress.setInfo("Converting input to shapefile: "+path)
                    tempfile = system.getTempFilename("shp")
                    ret = QgsVectorFileWriter.writeAsVectorFormat(processing.getObject(path), tempfile, "utf-8", None, "ESRI Shapefile")
                    assert(ret == QgsVectorFileWriter.NoError)
                    converted_inputs[name]=tempfile
                else:
                    converted_inputs[name]=path
        syntax["inputs"]=converted_inputs
        
        # figure out where the qgis python installation is
        qgisbase = os.path.dirname(os.path.dirname(sys.executable))
        pythonexe = qgisbase+os.sep+"bin"+os.sep+"python.exe"
        pythonbase = qgisbase+os.sep+"apps"+os.sep+"python27"+os.sep
        pythonpath = ";".join([pythonbase+x for x in ["","Lib","Lib/site-packages"]])
        
        retval = self.provider.runsdnacommand(syntax,self.provider.sdnapath,progress,pythonexe,pythonpath)
        
        if retval!=0:
            progress.setInfo("ERROR: PROCESS DID NOT COMPLETE SUCCESSFULLY")
        
class sDNAProvider(AlgorithmProvider):

    def installsdna(self):
        QMessageBox.critical(QDialog(),"sDNA: Error",
        """The QGIS sDNA plugin could not find your sDNA installation.  Please 
    - ensure sDNA version 3.0 or later is installed
    - ensure the sDNA installation folder is set correctly in 
      Processing -> Options -> Providers -> Spatial Design Network Analysis""")

    def getSupportedOutputVectorLayerExtensions(self):
        return ["shp"]
        
    def __init__(self):
        AlgorithmProvider.__init__(self)
        
        # activate provider by default
        self.activate = True
        
    SDNAFOLDERSETTING = "SDNA_FOLDER_SETTING"
            
    def initializeSettings(self):
        AlgorithmProvider.initializeSettings(self)
        ProcessingConfig.addSetting(Setting(self.getDescription(),sDNAProvider.SDNAFOLDERSETTING,
            "sDNA installation folder","c:\\Program Files (x86)\\sDNA",valuetype=Setting.FOLDER))
        
    def unload(self):
        AlgorithmProvider.unload(self)
        ProcessingConfig.removeSetting(sDNAProvider.SDNAFOLDERSETTING)
        
    def getName(self):
        return "sDNA"
        
    def getDescription(self):
        return "Spatial Design Network Analysis"

    def getIcon(self):
        return AlgorithmProvider.getIcon(self)

    def _loadAlgorithms(self):
        # create empty alglist now in case loading fails
        self.algs = []
        
        # import sDNAUISpec and runsdnacommand
        sdnarootdir = ProcessingConfig.getSetting(sDNAProvider.SDNAFOLDERSETTING)
        self.sdnapath = '"'+sdnarootdir+os.sep+"bin"+'"'
        QgsMessageLog.logMessage("sDNA root: "+str(sdnarootdir), 'sDNA')
        if not sdnarootdir in sys.path:
            sys.path.insert(0,sdnarootdir) # actualy python path not system path
        try:
            import sDNAUISpec,runsdnacommand
            reload(sDNAUISpec)
            reload(runsdnacommand)
            QgsMessageLog.logMessage("Successfully imported sDNA modules", 'sDNA')
        except ImportError, e:
            QgsMessageLog.logMessage(str(e), 'sDNA')
            self.installsdna()
            return
        self.runsdnacommand = runsdnacommand.runsdnacommand
        
        # load tools
        for toolclass in sDNAUISpec.get_tools():
            qgistool = SDNAAlgorithm(toolclass(),self)
            self.algs += [qgistool]
           

