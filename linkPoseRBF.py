import math
from maya.api import OpenMaya as om2
from maya     import cmds


def maya_useNewAPI():
    pass

    
class LinkPoseRBF(om2.MPxNode):
    MAIN_EPS   = 1e-9
    KERNEL_EPS = 1e-5
    
    TYPE_NAME = 'linkPoseRBF'
    Type_ID   = om2.MTypeId(0x3F8C1AE9)
    
    INPUT_DIMENSION  = None
    OUTPUT_DIMENSION = None
    RADIUS           = None
    KERNEL           = None
    NORMAL_OUTPUTS   = None
    
    DRIVER_INPUT_N = None
    
    TARGETS        = None
    POSE_INPUT_N   = None
    POSE_OUTPUT_M  = None
    
    REBUILD_FLAG = None
    
    OUTPUT_M = None
    
    
    def __init__(self):
        super().__init__()
        
        self.inDim  = 3
        self.outDim = 1
        self.radius = 1.0
        self.kernelType = 1
        
        self.poseInNMatrixs = []
        self.solvedWeights  = []
        
    @staticmethod    
    def inverseMatrix(matrix:list, eps:float=1.0e-10) -> list:
        n = len(matrix)
        identityMatrix = [[float(i == j) for i in range(n)] for j in range(n)]
        a = [[float(matrix[i][j]) for j in range(n)] for i in range(n)]

        for i in range(n):
            a[i][i] += LinkPoseRBF.MAIN_EPS

        for i in range(n):
            if a[i][i] == 0:
                for j in range(i+1, n):
                    if a[j][i] != 0:
                        a[i], a[j] = a[j], a[i]
                        identityMatrix [i], identityMatrix [j] = identityMatrix [j], identityMatrix [i]
                        break
                else:
                    raise ValueError('Matrix is singular and cannot be inverted')

            factor = a[i][i]
            if abs(factor) < eps:
                raise ValueError('Matrix is singular and cannot be inverted')

            for j in range(n):
                a[i][j] /= factor
                identityMatrix [i][j] /= factor

            for j in range(n):
                if j != i:
                    factor = a[j][i]
                    for k in range(n):
                        a[j][k] -= factor * a[i][k]
                        identityMatrix [j][k] -= factor * identityMatrix [i][k]

        return identityMatrix
        
    
    @staticmethod
    def getDistance(v1:list, v2:list) -> float:
        return math.sqrt(sum((a - b) * (a - b) for a, b in zip(v1, v2)))
        
    
    @staticmethod
    def _rbfLinear(distance:float, radius:float=1.0) -> float:
        if radius < LinkPoseRBF.KERNEL_EPS: radius = LinkPoseRBF.KERNEL_EPS
        val = 1.0 - (distance / radius)
        return max(0.0, val)
         
    @staticmethod
    def _rbfGaussian(distance:float, radius:float=1.0) -> float:
        if radius < LinkPoseRBF.KERNEL_EPS: radius = LinkPoseRBF.KERNEL_EPS
        val = distance / radius
        return math.exp(-val * val)
        
    @staticmethod
    def _rbfCubic(distance:float, radius:float=1.0) -> float:
        if radius < LinkPoseRBF.KERNEL_EPS: radius = LinkPoseRBF.KERNEL_EPS
        val = distance / radius
        if val >= 1.0: return 0.0 
        tmp = 1.0 - val
        return tmp * tmp * tmp
        
    @staticmethod
    def _rbfInverseMultiQuadratic(distance:float, radius:float=1.0) -> float:
        if radius < LinkPoseRBF.KERNEL_EPS: radius = LinkPoseRBF.KERNEL_EPS
        x = distance / radius
        return 1.0 / math.sqrt(1.0 + x * x)
        
    @staticmethod
    def _rbfQuintic(distance:float, radius:float=1.0) -> float:
        if radius < LinkPoseRBF.KERNEL_EPS: radius = LinkPoseRBF.KERNEL_EPS
        x = distance / radius
        if x >= 1.0: return 0.0 
        
        invX = 1.0 - x
        return invX * invX * invX * invX * invX 
        
    @staticmethod
    def calculateKernel(distance:float, radius:float, kernelType:int) -> float:
        if kernelType == 0:
            return LinkPoseRBF._rbfLinear(distance, radius)
        elif kernelType == 1:
            return LinkPoseRBF._rbfGaussian(distance, radius)
        elif kernelType == 2:
            return LinkPoseRBF._rbfCubic(distance, radius)
        elif kernelType == 3:
            return LinkPoseRBF._rbfInverseMultiQuadratic(distance, radius)
        elif kernelType == 4:
            return LinkPoseRBF._rbfQuintic(distance, radius)
        return LinkPoseRBF._rbfGaussian(distance, radius)
        
        
    @staticmethod
    def createDistanceMatrix(poses:list, radius:float=1, kernelType:int=1) -> list:
        N = len(poses)
        matrix = [[0.0] * N for _ in range(N)]
        
        for i in range(N):
            for j in range(N):
                if i == j:
                    matrix[i][j] = 1.0 + LinkPoseRBF.MAIN_EPS
                    continue
                
                distance = LinkPoseRBF.getDistance(poses[i], poses[j])
                rbfVal   = LinkPoseRBF.calculateKernel(distance, radius, kernelType)
                matrix[i][j] = rbfVal    
        return matrix
        
        
    @staticmethod    
    def multMatrix(A:list, B:list) -> list:
        result = [[0.0] * len(B[0]) for _ in range(len(A))]
        for i in range(len(A)):
            for j in range(len(B[0])):
                for k in range(len(B)):
                    result[i][j] += A[i][k] * B[k][j]
        return result
        
        
    def setup(self, dataBlock:om2.MDataBlock) -> bool:
        self.inDim :int     = dataBlock.inputValue(self.INPUT_DIMENSION).asInt()
        self.outDim:int     = dataBlock.inputValue(self.OUTPUT_DIMENSION).asInt()
        self.radius:float   = dataBlock.inputValue(self.RADIUS).asDouble()
        self.kernelType:int = dataBlock.inputValue(self.KERNEL).asInt()
        
        # 0 update targets in/out list
        targetsHandle = dataBlock.inputArrayValue(self.TARGETS)
        
        self.poseInNMatrixs  = []
        poseOutMMatrixs      = []
        
        for i in range(len(targetsHandle)):
            targetsHandle.jumpToPhysicalElement(i) 
            currentPose:om2.MDataHandle = targetsHandle.inputValue()
            # get sub inN list
            pInHandle     = om2.MArrayDataHandle(currentPose.child(self.POSE_INPUT_N))
            poseInNMatrix = [0.0 for i in range(self.inDim)]
            for _i in range(len(pInHandle)):
                pInHandle.jumpToPhysicalElement(_i)
                logicalIndex = pInHandle.elementLogicalIndex() # get index
                if logicalIndex < self.inDim:
                    poseInNMatrix[logicalIndex] = pInHandle.inputValue().asDouble()
            self.poseInNMatrixs.append(poseInNMatrix)
            
            # get sub outM list
            pOutHandle     = om2.MArrayDataHandle(currentPose.child(self.POSE_OUTPUT_M))
            poseOutMMatrix = [0.0 for i in range(self.outDim)]
            for _i in range(len(pOutHandle)):
                pOutHandle.jumpToPhysicalElement(_i)
                logicalIndex = pOutHandle.elementLogicalIndex() # get index
                if logicalIndex < self.outDim:
                    poseOutMMatrix[logicalIndex] = pOutHandle.inputValue().asDouble()
            poseOutMMatrixs.append(poseOutMMatrix)
            
        # 1 create distanceMatrix
        distanceMatrix = LinkPoseRBF.createDistanceMatrix(self.poseInNMatrixs, self.radius, self.kernelType)
        
        # 2 get inver distanceMatrix
        try:
            invertDistanceMatrix = LinkPoseRBF.inverseMatrix(distanceMatrix)
        except ValueError:
            om2.MGlobal.displayError("RBF Error: Singular Matrix!")
            return False
            
        # 3 cache Weights
        self.solvedWeights = LinkPoseRBF.multMatrix(invertDistanceMatrix, poseOutMMatrixs)
        
        dataBlock.outputValue(self.REBUILD_FLAG).setClean() # update
        om2.MGlobal.displayInfo('Rebuild OK')
        return True
        
        
    def getPoseActivations(self, driverVec:list, kernelType:int=1) -> list:
        rbfActivations = []
        
        for posepos in self.poseInNMatrixs:
            distance = LinkPoseRBF.getDistance(driverVec, posepos)
            rbfVal   = LinkPoseRBF.calculateKernel(distance, self.radius, kernelType)
            rbfActivations.append(rbfVal)
        
        return LinkPoseRBF.multMatrix([rbfActivations], self.solvedWeights)[0]
        
    
    @staticmethod
    def normalizeOutputValues(values: list) -> list:
        minVal = min(values)
        rangeSpan = 1.0 - minVal
        
        if abs(rangeSpan) < LinkPoseRBF.MAIN_EPS:
            rangeSpan = 1.0 
            
        remappedValues = []
        currentSum = 0.0
        
        for v in values:
            newV = (v - minVal) / rangeSpan
            
            remappedValues.append(newV)
            currentSum += newV

        finalDivisor = max(1.0, currentSum)

        if finalDivisor < LinkPoseRBF.MAIN_EPS:
            return [0.0] * len(values)
            
        return [v / finalDivisor for v in remappedValues]
        
    @staticmethod
    def normalizeOutputValues2(values: list) -> list:
        clamped = [max(0.0, v) for v in values]
        total   = sum(clamped)

        if total < LinkPoseRBF.MAIN_EPS:
            n = len(values)
            if n == 0:
                return values
            equal = 1.0 / n
            return [equal for _ in range(n)]

        return [v / total for v in clamped]
            
     
    def compute(self, plug:om2.MPlug, dataBlock:om2.MDataBlock) -> 'self':
        if plug != self.OUTPUT_M:
            return
            
        # 0 check rebuild state
        if not dataBlock.isClean(self.REBUILD_FLAG):
            if not self.setup(dataBlock):
                return
                
        # 1 get poseIn keys
        if not hasattr(self, 'poseInNMatrixs') or not self.poseInNMatrixs:
            dataBlock.setClean(plug)
            return
                
        # 2 get driver values
        dInHandle = dataBlock.inputArrayValue(self.DRIVER_INPUT_N)
        driverVec = [0.0 for i in range(self.inDim)]
        
        for i in range(len(dInHandle)):
            dInHandle.jumpToPhysicalElement(i)
            logicalIndex = dInHandle.elementLogicalIndex()
            if logicalIndex < self.inDim:
                driverVec[logicalIndex] = dInHandle.inputValue().asDouble()
                
        # 3 get activations
        finalOutputVec:list = self.getPoseActivations(driverVec, self.kernelType)
        
        # 4 check norm state
        if dataBlock.inputValue(self.NORMAL_OUTPUTS).asBool():
            finalOutputVec = LinkPoseRBF.normalizeOutputValues2(finalOutputVec)
        
        # 5 set outputs
        outArrayHandle = dataBlock.outputArrayValue(self.OUTPUT_M)
        builder        = outArrayHandle.builder()
        
        for i in range(self.outDim):
            outHandle = builder.addElement(i)
            val = 0.0
            if i < len(finalOutputVec):
                val = finalOutputVec[i]
            outHandle.setDouble(val)
            
        outArrayHandle.set(builder)
        outArrayHandle.setAllClean()
        dataBlock.setClean(plug)
        
    
    @classmethod
    def creator(cls) -> 'LinkPoseRBF':
        return cls()
    
    
    @classmethod
    def initialize(cls):
        numericAttr  = om2.MFnNumericAttribute()
        compoundAttr = om2.MFnCompoundAttribute()
        enumAttr     = om2.MFnEnumAttribute()
        
        
        cls.INPUT_DIMENSION = numericAttr.create('inDim', 'iD', om2.MFnNumericData.kInt, 3)
        numericAttr.setMin(1)
        numericAttr.setSoftMin(1); numericAttr.setSoftMax(10)
        numericAttr.keyable  = True 

        cls.OUTPUT_DIMENSION = numericAttr.create('outDim', 'oD', om2.MFnNumericData.kInt, 3)
        numericAttr.setMin(1)
        numericAttr.setSoftMin(1); numericAttr.setSoftMax(10)
        numericAttr.keyable  = True 
        
        cls.KERNEL = enumAttr.create('kernel', 'knl', 1)
        enumAttr.addField('Linear', 0)
        enumAttr.addField('Gaussian', 1)
        enumAttr.addField('Cubic', 2)
        enumAttr.addField('Inverse Multi-Quadratic', 3)
        enumAttr.addField('Quintic', 4)
        enumAttr.keyable  = True 
        
        cls.RADIUS = numericAttr.create('radius', 'r', om2.MFnNumericData.kDouble, 1.0)
        numericAttr.setMin(LinkPoseRBF.KERNEL_EPS)
        numericAttr.setSoftMin(LinkPoseRBF.KERNEL_EPS); numericAttr.setSoftMax(10.0)
        numericAttr.keyable  = True 
        
        cls.NORMAL_OUTPUTS = numericAttr.create('normOutput', 'nmo', om2.MFnNumericData.kBoolean, False)
        numericAttr.keyable  = True 

     
        cls.DRIVER_INPUT_N = numericAttr.create('driverInN', 'dIN', om2.MFnNumericData.kDouble, 0.0)
        numericAttr.keyable  = True 
        numericAttr.array = True
 

        cls.POSE_INPUT_N = numericAttr.create('poseInN', 'pIN', om2.MFnNumericData.kDouble, 0.0)
        numericAttr.array = True
        
        cls.POSE_OUTPUT_M = numericAttr.create('poseOutM', 'pOM', om2.MFnNumericData.kDouble, 0.0)
        numericAttr.array = True
        
        cls.TARGETS = compoundAttr.create('targets', 'tags')
        compoundAttr.array = True
        compoundAttr.keyable  = True 
        compoundAttr.addChild(cls.POSE_INPUT_N)
        compoundAttr.addChild(cls.POSE_OUTPUT_M)
        
        
        cls.REBUILD_FLAG = numericAttr.create('rbFlag', 'rF', om2.MFnNumericData.kBoolean, True)
        numericAttr.hidden = True
    
        
        cls.OUTPUT_M = numericAttr.create('outM', 'oM', om2.MFnNumericData.kDouble, 0.0)
        numericAttr.array = True
        numericAttr.usesArrayDataBuilder = True
        numericAttr.writable = False
        
        cls.addAttribute(cls.INPUT_DIMENSION)
        cls.addAttribute(cls.OUTPUT_DIMENSION)
        cls.addAttribute(cls.KERNEL)
        cls.addAttribute(cls.RADIUS)
        cls.addAttribute(cls.NORMAL_OUTPUTS)
        cls.addAttribute(cls.DRIVER_INPUT_N)
        cls.addAttribute(cls.TARGETS)
        cls.addAttribute(cls.REBUILD_FLAG)
        cls.addAttribute(cls.OUTPUT_M)
        
        # Trigger the rebuild signal
        cls.attributeAffects(cls.NORMAL_OUTPUTS,   cls.REBUILD_FLAG)
        cls.attributeAffects(cls.KERNEL,           cls.REBUILD_FLAG)
        cls.attributeAffects(cls.RADIUS,           cls.REBUILD_FLAG)
        cls.attributeAffects(cls.INPUT_DIMENSION,  cls.REBUILD_FLAG)
        cls.attributeAffects(cls.OUTPUT_DIMENSION, cls.REBUILD_FLAG)
        cls.attributeAffects(cls.TARGETS,          cls.REBUILD_FLAG)
        
        cls.attributeAffects(cls.NORMAL_OUTPUTS,   cls.OUTPUT_M)
        cls.attributeAffects(cls.KERNEL,           cls.OUTPUT_M)
        cls.attributeAffects(cls.RADIUS,           cls.OUTPUT_M)
        cls.attributeAffects(cls.INPUT_DIMENSION,  cls.OUTPUT_M)
        cls.attributeAffects(cls.OUTPUT_DIMENSION, cls.OUTPUT_M)
        cls.attributeAffects(cls.DRIVER_INPUT_N,   cls.OUTPUT_M)
        cls.attributeAffects(cls.TARGETS,          cls.OUTPUT_M)
        cls.attributeAffects(cls.REBUILD_FLAG,     cls.OUTPUT_M)


def initializePlugin(plugin:om2.MObject):
    vendor      = 'Link Rigger'
    version     = '1.0.0'
    plugFn = om2.MFnPlugin(plugin, vendor, version)
    
    try:
        plugFn.registerNode(LinkPoseRBF.TYPE_NAME, 
                            LinkPoseRBF.Type_ID,
                            LinkPoseRBF.creator,
                            LinkPoseRBF.initialize,
                            om2.MPxNode.kDependNode)
    except:
        om2.MGlobal.displayError(f'Failed to register node: {LinkPoseRBF.TYPE_NAME}')
 

def uninitializePlugin(plugin:om2.MObject):
    plugFn = om2.MFnPlugin(plugin)
    try:
        plugFn.deregisterNode(LinkPoseRBF.Type_ID)
    except:
        om2.MGlobal.displayError(f'Failed to register node: {LinkPoseRBF.TYPE_NAME}')
