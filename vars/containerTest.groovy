// Test stage: optional `just test`, run if the recipe is defined
def call(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def needBuilder = config.needBuilder ?: false

    dir(workDir) {
        if (needBuilder) {
            sh "if just --summary | grep -q test; then just use-builder test; fi"
        } else {
            sh "if just --summary | grep -q test; then just test; fi"
        }
    }
}
