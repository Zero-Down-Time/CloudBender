// Push stage: `just container::push <registry> [imageName]` + rm-remote-untagged
def call(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''
    def registry  = config.registry  ?: ''

    if (!registry) {
        error("'registry' is required in the justContainer config (e.g. 'public.ecr.aws/<alias>' or '<account>.dkr.ecr.<region>.amazonaws.com')")
    }

    def imageArg = imageName ? " '${imageName}'" : ''

    dir(workDir) {
        sh "just container::push '${registry}'${imageArg}"
        sh "just container::rm-remote-untagged '${registry}'${imageArg}"
    }
}
