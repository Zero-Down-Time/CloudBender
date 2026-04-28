// Build stage: changeSet gate + `just container::build`
def call(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def tmpDir      = config.tmpDir      ?: '_tmp'
    def imageName   = config.imageName   ?: ''
    def buildOnly   = config.buildOnly   ?: ['.*']
    def forceBuild  = config.forceBuild != null ? config.forceBuild : (config.force_build ?: false)
    def needBuilder = config.needBuilder ?: false
    def debug       = config.debug       ?: false
    def stashName   = config.stashName   ?: 'changeSet'

    def imageArg = imageName ? " '${imageName}'" : ''

    dir(workDir) {
        unstash stashName
        def files = readJSON file: "${tmpDir}/changeSet.json"

        if (forceBuild || gitea.pathsChanged(files: files, patterns: buildOnly, debug: debug)) {
            if (needBuilder) {
                sh "just use-builder build release"
            }
            sh "just container::build${imageArg}"
        } else {
            echo "No changed files matching any of: ${buildOnly.join(', ')}. No build required."
            currentBuild.description = 'SKIP'
        }
    }
}
