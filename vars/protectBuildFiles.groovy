// Overwrite build files from the target/origin branch
def call(List files = ['Makefile', '.justfile']) {
    if (!env.CHANGE_ID) {
        echo "Not a PR build, skipping build file protection"
        return
    }

    sh "git fetch origin ${env.CHANGE_TARGET}"
    sh "git checkout origin/${env.CHANGE_TARGET} -- ${files.join(' ')}"
}
