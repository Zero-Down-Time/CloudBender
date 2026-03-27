// just based container builder

def call(Map config=[:]) {
    def buildOnly = config.buildOnly ?: ['.*']
    def debug = config.debug ?: false
    def force_build = config.force_build ?: false
    def needBuilder = config.needBuilder ?: false
    def imageName = config.imageName ?: ""
    def scanFail = config.scanFail != null ? config.scanFail : true

    pipeline {
      options {
        disableConcurrentBuilds()
      }
      agent {
        node {
          label 'podman-aws-grype'
        }
      }
      environment {
        TMP_DIR = "_tmp"
      }
      stages {
        stage('Prepare') {
          steps {
            // create and stash changeSet
            script {
              def files = gitea.getChangeset(debug: debug)
              writeJSON file: "${TMP_DIR}/changeSet.json", json: files
              stash includes: "${TMP_DIR}/changeSet.json", name: 'changeSet'
            }

            // Overwrite build files from the target/origin branch
            protectBuildFiles(['.justfile', '.ci/**'])

            script {
              // build reports dir outside workspace to not pollute the source tree
              sh "mkdir -p ${TMP_DIR}"

              // Build project specific builder
              if (needBuilder) {
                sh "just update-builder"
                sh "just use-builder prepare"
              } else {
                sh "just prepare"
              }
            }
          }
        }

        stage('Lint') {
          steps {
            // Scan for secrets first thing
            sh "betterleaks dir . --validation false --exit-code 0 --no-banner --no-color --report-path ${TMP_DIR}/betterleaks-src-report.json --report-format sarif"
            recordIssues (
              enabledForFailure: true,
              sourceCodeRetention: 'NEVER',
              skipPublishingChecks: true,
              quiet: true,
              skipBlames: true,
              qualityGates: [[threshold: 1, type: 'TOTAL_ERROR', criticality: scanFail ? 'FAILURE' : 'NOTE']],
              tools: [
                sarif(pattern: "${TMP_DIR}/betterleaks-src-report.json", name: 'Source Leaks', id: 'source-leaks')
              ]
            )

            script {
              if (needBuilder) {
                sh "just use-builder lint"
              } else {
                sh "just lint"
              }
            }
          }
        }

        // Build using rootless podman
        stage('Build') {
          when {
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            script {
              unstash 'changeSet'
              def files = readJSON file: "${TMP_DIR}/changeSet.json"

              if (force_build || gitea.pathsChanged(files: files, patterns: buildOnly, debug: debug)) {
                if (needBuilder) {
                  sh "just use-builder build release"
                }
                sh "just container::build ${imageName}"
              } else {
                echo("No changed files matching any of: ${buildOnly.join(', ')}. No build required.")
                currentBuild.description = 'SKIP'
              }
            }
          }
        }

        stage('Test') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            sh "echo"
            // sh "just container::test"
          }
        }

        // Scan using grype and evaluate results via quality gates
        stage('Scan') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            // Scan built container for secrets vulnerabilities
            sh "GRYPE_LOG_QUIET=true GRYPE_OUTPUT=json GRYPE_FILE=${TMP_DIR}/grype-report.json BETTERLEAKS_FILE=${TMP_DIR}/betterleaks-image-report.json \
              just container::scan ${TMP_DIR} ${imageName}"
            recordIssues (
              enabledForFailure: true,
              sourceCodeRetention: 'NEVER',
              skipPublishingChecks: true,
              quiet: true,
              skipBlames: true,
              qualityGates: [[threshold: 1, type: 'TOTAL_ERROR', criticality: scanFail ? 'FAILURE' : 'NOTE']],
              tools: [
                grype(pattern: "${TMP_DIR}/grype-report.json")
              ]
            )

            recordIssues (
              enabledForFailure: true,
              sourceCodeRetention: 'NEVER',
              skipPublishingChecks: true,
              quiet: true,
              skipBlames: true,
              qualityGates: [[threshold: 1, type: 'TOTAL_ERROR', criticality: scanFail ? 'FAILURE' : 'NOTE']],
              tools: [
                sarif(pattern: "${TMP_DIR}/betterleaks-image-report.json", id: 'image-leaks', name: 'Image Leaks')
              ]
            )
          }
        }

        // Push to container registry if not PR and scans passed
        // incl. basic registry retention removing any untagged images
        stage('Push') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
            not { changeRequest() }
          }
          steps {
            sh "just container::push ${imageName}"
            sh "just container::rm-remote-untagged ${imageName}"
          }
        }

        // generic clean, dont clean if FAILURE to speed up quick fix cycles via caching
        stage('cleanup') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            sh "just container::clean ${imageName}"
          }
        }
      }

      post {
        cleanup {
          sh "rm -rf ${TMP_DIR}"
        }
      }
    }
}
