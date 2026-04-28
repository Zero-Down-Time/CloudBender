// just based container builder — thin wrapper composing the per-stage helpers

def call(Map config = [:]) {
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
            containerPrepare(config)
          }
        }

        stage('Lint') {
          steps {
            containerLint(config)
          }
        }

        stage('Build') {
          when {
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            containerBuild(config)
          }
        }

        stage('Test') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            containerTest(config)
          }
        }

        stage('Scan') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            containerScan(config)
          }
        }

        stage('Push') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
            not { changeRequest() }
          }
          steps {
            containerPush(config)
          }
        }

        stage('cleanup') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            containerClean(config)
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
