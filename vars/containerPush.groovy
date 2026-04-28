// Push stage: `just container::push` + `just container::rm-remote-untagged`
def call(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''

    dir(workDir) {
        sh "just container::push '${imageName}'"
        sh "just container::rm-remote-untagged '${imageName}'"
    }
}
