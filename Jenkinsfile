pipeline {
  agent { node { label 'podman-aws-trivy' } }

  stages {
    stage('Prepare'){
        // get tags
        steps {
            sh 'git fetch --tags ${GIT_URL} +refs/heads/${BRANCH_NAME}:refs/remotes/origin/${BRANCH_NAME}'
        }
    }

    // Build using rootless podman
    stage('Build'){
        steps {
            sh 'make build'
        }
    }

    stage('Test'){
        steps {
            sh 'make test'
        }
    }

    // Scan via trivy
    stage('Scan'){
        environment { 
            TRIVY_FORMAT = "template"
            TRIVY_OUTPUT = "reports/trivy.html"
        }
        steps {
            sh 'mkdir -p reports'
            sh 'make scan'
            publishHTML target : [
                allowMissing: true,
                alwaysLinkToLastBuild: true,
                keepAll: true,
                reportDir: 'reports',
                reportFiles: 'trivy.html',
                reportName: 'TrivyScan',
                reportTitles: 'TrivyScan'
            ]

            // Scan again and fail on CRITICAL vulns
            sh 'TRIVY_EXIT_CODE=1 TRIVY_SEVERITY=CRITICAL make scan'
        }
    }

    // Push to ECR
    stage('Push'){
        steps {
            sh 'make push'
        }
    }
  }
}
