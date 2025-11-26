/**
required plugins:
- HTTP Request
- Pipeline Utility Steps
- Credentials Plugin
**/

/**
 * Parse GIT_URL to extract Gitea URL, owner, and repo
 */
def parseGitUrl(String gitUrl) {
    // Handle both HTTPS and SSH formats
    // https://gitea.example.com/owner/repo.git
    // git@gitea.example.com:owner/repo.git

    def matcher
    if (gitUrl.startsWith('http')) {
        matcher = gitUrl =~ /^(https?:\/\/[^\/]+)\/([^\/]+)\/([^\/]+?)(\.git)?$/
    } else {
        matcher = gitUrl =~ /^git@([^:]+):([^\/]+)\/([^\/]+?)(\.git)?$/
    }

    if (matcher.matches()) {
        def baseUrl = gitUrl.startsWith('http') ? matcher[0][1] : "https://${matcher[0][1]}"
        return [
            giteaUrl: baseUrl,
            owner: matcher[0][2],
            repo: matcher[0][3]
        ]
    }

    return null
}

/**
 * Get changeset (list of changed files) from Gitea using Jenkins SCM credentials
 */
def getChangeset(Map config = [:]) {
    // Parse GIT_URL for defaults
    def parsed = env.GIT_URL ? parseGitUrl(env.GIT_URL) : [:]

    def giteaUrl = config.giteaUrl ?: parsed.giteaUrl ?: env.GITEA_URL
    def credentialsId = config.credentialsId ?: 'gitea-jenkins-password'
    def owner = config.owner ?: parsed.owner
    def repo = config.repo ?: parsed.repo
    def prNumber = config.prNumber ?: env.CHANGE_ID
    def commitSha = config.commitSha ?: env.GIT_COMMIT
    def debug = config.debug ?: false

    if (!giteaUrl || !owner || !repo) {
        error("Missing required parameters: giteaUrl, owner, repo. GIT_URL: ${env.GIT_URL}")
    }

    def changedFiles = []

    withCredentials([usernamePassword(credentialsId: credentialsId, usernameVariable: 'GITEA_USERNAME', passwordVariable: 'GITEA_TOKEN')]) {
        if (prNumber) {
            changedFiles = getPRChangeset(giteaUrl, env.GITEA_TOKEN, owner, repo, prNumber, debug)
        } else if (commitSha) {
            changedFiles = getCommitChangeset(giteaUrl, env.GITEA_TOKEN, owner, repo, commitSha, debug)
        } else {
            def currentSha = env.GIT_COMMIT ?: sh(script: 'git rev-parse HEAD', returnStdout: true).trim()
            changedFiles = getCommitChangeset(giteaUrl, env.GITEA_TOKEN, owner, repo, currentSha, debug)
        }
    }

    return changedFiles
}

/**
 * Get changed files for a Pull Request
 */
def getPRChangeset(String giteaUrl, String token, String owner, String repo, String prNumber, Boolean debug) {
    def apiUrl = "${giteaUrl}/api/v1/repos/${owner}/${repo}/pulls/${prNumber}/files"
    def authHeader = "token " + token

    def response = httpRequest(
        url: apiUrl,
        httpMode: 'GET',
        customHeaders: [[name: 'Authorization', value: authHeader, maskValue: true]],
        contentType: 'APPLICATION_JSON',
        validResponseCodes: '200',
        quiet: !debug
    )

    def files = readJSON text: response.content
    return files.collect { it.filename }
}

/**
 * Get changed files for a specific commit
 */
def getCommitChangeset(String giteaUrl, String token, String owner, String repo, String sha, Boolean debug) {
    def apiUrl = "${giteaUrl}/api/v1/repos/${owner}/${repo}/git/commits/${sha}"
    def authHeader = "token " + token

    def response = httpRequest(
        url: apiUrl,
        httpMode: 'GET',
        customHeaders: [[name: 'Authorization', value: authHeader, maskValue: true]],
        contentType: 'APPLICATION_JSON',
        validResponseCodes: '200',
        quiet: !debug
    )

    def commit = readJSON text: response.content
    def parentSha = commit.parents ? commit.parents[0].sha : null

    if (!parentSha) {
        return getCommitFiles(giteaUrl, token, owner, repo, sha, debug)
    }

    return getCommitDiff(giteaUrl, token, owner, repo, parentSha, sha, debug)
}

/**
 * Get diff between two commits
 */
def getCommitDiff(String giteaUrl, String token, String owner, String repo, String base, String head, Boolean debug) {
    def apiUrl = "${giteaUrl}/api/v1/repos/${owner}/${repo}/compare/${base}...${head}"
    def authHeader = "token " + token

    def response = httpRequest(
        url: apiUrl,
        httpMode: 'GET',
        customHeaders: [[name: 'Authorization', value: authHeader, maskValue: true]],
        contentType: 'APPLICATION_JSON',
        validResponseCodes: '200',
        quiet: !debug
    )

    def diff = readJSON text: response.content
    def allFiles = []

    // Iterate over all commits and gather files
    if (diff.commits) {
        diff.commits.each { commit ->
            if (commit.files) {
                commit.files.each { file ->
                    if (!allFiles.contains(file.filename)) {
                        allFiles.add(file.filename)
                    }
                }
            }
        }
    }

    return allFiles
}

/**
 * Get all files in a commit (for initial commit)
 */
def getCommitFiles(String giteaUrl, String token, String owner, String repo, String sha, Boolean debug) {
    def apiUrl = "${giteaUrl}/api/v1/repos/${owner}/${repo}/git/trees/${sha}?recursive=true"
    def authHeader = "token " + token

    def response = httpRequest(
        url: apiUrl,
        httpMode: 'GET',
        customHeaders: [[name: 'Authorization', value: authHeader, maskValue: true]],
        contentType: 'APPLICATION_JSON',
        validResponseCodes: '200',
        quiet: !debug
    )

    def tree = readJSON text: response.content
    return tree.tree ? tree.tree.findAll { it.type == 'blob' }.collect { it.path } : []
}

/**
 * Filter changeset by pattern
 */
def filterChangeset(List files, String pattern) {
    return files.findAll { it =~ pattern }
}

/**
 * Check if specific paths changed
 */
def pathsChanged(List files, List patterns) {
    return patterns.any { pattern ->
        files.any { file -> file =~ pattern }
    }
}

return this
