// Lint stage: `just scan-src` source secrets scan + `just lint`
def call(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def tmpDir      = config.tmpDir      ?: '_tmp'
    def needBuilder = config.needBuilder ?: false
    def scanFail    = config.scanFail != null ? config.scanFail : true

    dir(workDir) {
        withEnv(["BETTERLEAKS_SRC_FILE=${tmpDir}/betterleaks-src-report.json"]) {
            sh "if just --summary | grep -q scan-src; then just scan-src; fi"
        }

        recordIssues(
            enabledForFailure: true,
            sourceCodeRetention: 'NEVER',
            skipPublishingChecks: true,
            quiet: true,
            skipBlames: true,
            qualityGates: [[threshold: 1, type: 'TOTAL_ERROR', criticality: scanFail ? 'FAILURE' : 'NOTE']],
            tools: [
                sarif(pattern: "${tmpDir}/betterleaks-src-report.json", id: 'source-leaks', name: 'Source Leaks')
            ]
        )

        if (needBuilder) {
            sh "if just --summary | grep -q lint; then just use-builder lint; fi"
        } else {
            sh "if just --summary | grep -q lint; then just lint; fi"
        }
    }
}
