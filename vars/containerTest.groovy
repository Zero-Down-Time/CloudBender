// Test stage: placeholder for `just test` / `just container::test`
def call(Map config = [:]) {
    def workDir = config.workDir ?: '.'

    dir(workDir) {
        sh "echo"
        // sh "if just --summary | grep -q container::test; then just container::test; fi"
    }
}
