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
            script { container.prepare(config) }
          }
        }

        stage('Lint') {
          steps {
            script { container.lint(config) }
          }
        }

        stage('Build') {
          when {
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            script { container.build(config) }
          }
        }

        stage('Test') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            script { container.test(config) }
          }
        }

        stage('Scan') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            script { container.scan(config) }
          }
        }

        stage('Push') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
            not { changeRequest() }
          }
          steps {
            script { container.push(config) }
          }
        }

        stage('cleanup') {
          when {
            expression { currentBuild.description != 'SKIP' }
            expression { currentBuild.currentResult != 'FAILURE' }
          }
          steps {
            script { container.clean(config) }
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
