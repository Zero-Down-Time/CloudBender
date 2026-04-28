// Scan stage: `just container::scan` + grype/betterleaks recordIssues
def call(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def tmpDir    = config.tmpDir    ?: '_tmp'
    def imageName = config.imageName ?: ''
    def scanFail  = config.scanFail != null ? config.scanFail : true
    def imageArg  = imageName ? " '${imageName}'" : ''

    dir(workDir) {
        withEnv([
            "GRYPE_LOG_QUIET=true",
            "GRYPE_OUTPUT=json",
            "GRYPE_FILE=${tmpDir}/grype-report.json",
            "BETTERLEAKS_FILE=${tmpDir}/betterleaks-image-report.json",
        ]) {
            sh "just container::scan '${tmpDir}'${imageArg}"
        }

        recordIssues(
            enabledForFailure: true,
            sourceCodeRetention: 'NEVER',
            skipPublishingChecks: true,
            quiet: true,
            skipBlames: true,
            qualityGates: [[threshold: 1, type: 'TOTAL_ERROR', criticality: scanFail ? 'FAILURE' : 'NOTE']],
            tools: [
                grype(pattern: "${tmpDir}/grype-report.json")
            ]
        )

        recordIssues(
            enabledForFailure: true,
            sourceCodeRetention: 'NEVER',
            skipPublishingChecks: true,
            quiet: true,
            skipBlames: true,
            qualityGates: [[threshold: 1, type: 'TOTAL_ERROR', criticality: scanFail ? 'FAILURE' : 'NOTE']],
            tools: [
                sarif(pattern: "${tmpDir}/betterleaks-image-report.json", id: 'image-leaks', name: 'Image Leaks')
            ]
        )
    }
}
