// Lint stage: `just lint`
def call(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def needBuilder = config.needBuilder ?: false

    dir(workDir) {
        if (needBuilder) {
            sh "if just --summary | grep -q lint; then just use-builder lint; fi"
        } else {
            sh "if just --summary | grep -q lint; then just lint; fi"
        }
    }
}
