// Push stage: `just container::push` + `just container::rm-remote-untagged`
def call(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''
    def registry  = config.registry  ?: ''
    def imageArg  = imageName ? " '${imageName}'" : ''
    def envVars   = registry ? ["REGISTRY=${registry}"] : []

    dir(workDir) {
        withEnv(envVars) {
            sh "just container::push${imageArg}"
            sh "just container::rm-remote-untagged${imageArg}"
        }
    }
}
