// Container build stages — composed by justContainer.groovy.
// Each method is invoked as `container.<stage>(config)`.

// Prepare stage: gather changeSet, gate downstream stages on buildOnly,
// protect build files, run `just prepare`. Sets currentBuild.description = 'SKIP'
// when no changed files match buildOnly (and forceBuild is not set).
def prepare(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def tmpDir      = config.tmpDir      ?: '_tmp'
    def debug       = config.debug       ?: false
    def needBuilder = config.needBuilder ?: false
    def buildOnly   = config.buildOnly   ?: ['.*']
    def forceBuild  = config.forceBuild != null ? config.forceBuild : (config.force_build ?: false)
    def justfilePath    = workDir == '.' ? '.justfile' : "${workDir}/.justfile"
    def jenkinsfilePath = workDir == '.' ? 'Jenkinsfile' : "${workDir}/Jenkinsfile"
    def protect     = config.protect     ?: [justfilePath, jenkinsfilePath, '.ci/**']

    def files = gitea.getChangeset(debug: debug)

    if (!forceBuild && !gitea.pathsChanged(files: files, patterns: buildOnly, debug: debug)) {
        echo "No changed files matching any of: ${buildOnly.join(', ')}. Skipping downstream stages."
        currentBuild.description = 'SKIP'
        return
    }

    protectBuildFiles(protect)

    dir(workDir) {
        sh "mkdir -p '${tmpDir}'"
        if (needBuilder) {
            sh "just update-builder"
            sh "if just --summary | grep -q prepare; then just use-builder prepare; fi"
        } else {
            sh "if just --summary | grep -q prepare; then just prepare; fi"
        }
    }
}

// Lint stage: `just scan-src` source secrets scan + `just lint`
def lint(Map config = [:]) {
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
            sh "if just --summary | grep -q fmt; then just use-builder fmt release; fi"
            sh "if just --summary | grep -q lint; then just use-builder lint release; fi"
        } else {
            sh "if just --summary | grep -q fmt; then just fmt release; fi"
            sh "if just --summary | grep -q lint; then just lint release; fi"
        }
    }
}

// Build stage: `just container::build`. The buildOnly/forceBuild gate runs
// in `prepare`; if it short-circuits, this stage is skipped via the SKIP flag.
def build(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def imageName   = config.imageName   ?: ''
    def needBuilder = config.needBuilder ?: false

    def imageArg = imageName ? " '${imageName}'" : ''
    String tag

    dir(workDir) {
        if (needBuilder) {
            sh "just use-builder build release"
        }
        sh "just container::build${imageArg}"
        tag = sh(returnStdout: true, script: 'just container::_print-tag').trim()
    }
    return tag
}

// Test stage: optional `just test`, run if the recipe is defined
def test(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def needBuilder = config.needBuilder ?: false

    dir(workDir) {
        if (needBuilder) {
            sh "if just --summary | grep -q test; then just use-builder test release; fi"
        } else {
            sh "if just --summary | grep -q test; then just test release; fi"
        }
    }
}

// Scan stage: `just container::scan` + grype/betterleaks recordIssues
def scan(Map config = [:]) {
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

// Push stage: `just container::push <registry> [imageName]` + rm-remote-untagged.
// Returns the pushed tag (the value of git_tag the push recipe used).
def push(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''
    def registry  = config.registry  ?: ''

    if (!registry) {
        error("'registry' is required in the justContainer config (e.g. 'public.ecr.aws/<alias>' or '<account>.dkr.ecr.<region>.amazonaws.com')")
    }

    def imageArg = imageName ? " '${imageName}'" : ''
    String tag

    dir(workDir) {
        sh "just container::push '${registry}'${imageArg}"
        sh "just container::rm-remote-untagged '${registry}'${imageArg}"
        tag = sh(returnStdout: true, script: 'just container::_print-tag').trim()
    }
    return tag
}

// Cleanup stage: `just container::clean`
def clean(Map config = [:]) {
    def workDir   = config.workDir   ?: '.'
    def imageName = config.imageName ?: ''
    def imageArg  = imageName ? " '${imageName}'" : ''

    dir(workDir) {
        sh "just container::clean${imageArg}"
    }
}

// Post-pipeline: remove the reusable builder container created by `use-builder`.
// Called from `post.cleanup` so the container is torn down even on abort/failure.
def cleanBuilder(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def needBuilder = config.needBuilder ?: false
    if (!needBuilder) return

    dir(workDir) {
        sh "just clean-builder"
    }
}

return this
