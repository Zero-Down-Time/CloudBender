// Container build stages — composed by justContainer.groovy.
// Each method is invoked as `container.<stage>(config)`.

// Prepare stage: gather changeSet, protect build files, run `just prepare`
def prepare(Map config = [:]) {
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
            sh "if just --summary | grep -q lint; then just use-builder lint; fi"
        } else {
            sh "if just --summary | grep -q lint; then just lint; fi"
        }
    }
}

// Build stage: changeSet gate + `just container::build`. Returns the built tag,
// or null when the stage short-circuits (sets currentBuild.description = 'SKIP').
def build(Map config = [:]) {
    def workDir     = config.workDir     ?: '.'
    def tmpDir      = config.tmpDir      ?: '_tmp'
    def imageName   = config.imageName   ?: ''
    def buildOnly   = config.buildOnly   ?: ['.*']
    def forceBuild  = config.forceBuild != null ? config.forceBuild : (config.force_build ?: false)
    def needBuilder = config.needBuilder ?: false
    def debug       = config.debug       ?: false
    def stashName   = config.stashName   ?: 'changeSet'

    def imageArg = imageName ? " '${imageName}'" : ''
    String tag = null

    dir(workDir) {
        unstash stashName
        def files = readJSON file: "${tmpDir}/changeSet.json"

        if (forceBuild || gitea.pathsChanged(files: files, patterns: buildOnly, debug: debug)) {
            if (needBuilder) {
                sh "just use-builder build release"
            }
            sh "just container::build${imageArg}"
            tag = sh(returnStdout: true, script: 'just container::_print-tag').trim()
        } else {
            echo "No changed files matching any of: ${buildOnly.join(', ')}. No build required."
            currentBuild.description = 'SKIP'
        }
    }
    return tag
}

// Test stage: optional `just test`, run if the recipe is defined
def test(Map config = [:]) {
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

return this
