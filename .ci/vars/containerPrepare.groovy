// Prepare stage: gather changeSet, protect build files, run `just prepare`
def call(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def tmpDir      = config.tmpDir      ?: '_tmp'
    def debug       = config.debug       ?: false
    def needBuilder = config.needBuilder ?: false
    def justfilePath    = workDir == '.' ? '.justfile' : "${workDir}/.justfile"
    def jenkinsfilePath = workDir == '.' ? 'Jenkinsfile' : "${workDir}/Jenkinsfile"
    def protect     = config.protect     ?: [justfilePath, jenkinsfilePath, '.ci/**']
    def stashName   = config.stashName   ?: 'changeSet'

    def files = gitea.getChangeset(debug: debug)

    dir(workDir) {
        sh "mkdir -p '${tmpDir}'"
        writeJSON file: "${tmpDir}/changeSet.json", json: files
        stash includes: "${tmpDir}/changeSet.json", name: stashName
    }

    protectBuildFiles(protect)

    dir(workDir) {
        if (needBuilder) {
            sh "just update-builder"
            sh "if just --summary | grep -q prepare; then just use-builder prepare; fi"
        } else {
            sh "if just --summary | grep -q prepare; then just prepare; fi"
        }
    }
}
