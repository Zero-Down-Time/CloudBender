// Push stage: `just container::push` + `just container::rm-remote-untagged`
def call(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''
    def imageArg  = imageName ? " '${imageName}'" : ''

    dir(workDir) {
        sh "just container::push${imageArg}"
        sh "just container::rm-remote-untagged${imageArg}"
    }
}
