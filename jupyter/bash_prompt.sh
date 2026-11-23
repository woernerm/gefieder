# The Lab terminal's prompt: user, folder and git branch, as git-prompt.sh documents it,
# in Ubuntu's colours. The folder is shown from the SQLMesh project down, which is also as
# far up as cd goes: everything a person works on is inside it, and the full path is long
# and the same for everyone.
#
# Through PROMPT_COMMAND rather than PS1, because ~/.bashrc runs after this file and sets
# PS1 again; __git_ps1 in this mode rebuilds PS1 before every prompt instead.
. /usr/lib/git-core/git-sh-prompt
PROMPT_COMMAND='__git_ps1 "\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\${PWD#\${TERMINAL_ROOT%/*}/}\[\033[00m\]" "\$ "'

# A guard rather than a jail -- a script can still leave -- so that cd .., cd ~ or a
# terminal opened above the project land on the project, as cd .. at / stays at /.
if [ -n "${TERMINAL_ROOT-}" ]; then
  cd() {
    builtin cd "$@" || return
    case "$PWD/" in
      "$TERMINAL_ROOT"/*) ;;
      *) builtin cd "$TERMINAL_ROOT" ;;
    esac
  }
  cd .
fi
