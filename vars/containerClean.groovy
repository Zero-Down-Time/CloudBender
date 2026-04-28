// Cleanup stage: `just container::clean`
def call(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''
    def imageArg  = imageName ? " '${imageName}'" : ''

    dir(workDir) {
        sh "just container::clean${imageArg}"
    }
}
