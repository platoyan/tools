{
  description = "My Python project";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

    # nixpkgs，是 Nix 生态系统的"包仓库"
  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
	    # system, 是一个字符串, 代表操作系统+CPU架构的组合:如aarch64-darwin
      let
	      # 当前平台的所有包集合
        pkgs = nixpkgs.legacyPackages.${system};
        # 选择 Python 版本：python311 / python312 / python313
        python = pkgs.python313;
        tesseractWithLangs = pkgs.tesseract.override {
          enableLanguages = [ "eng" "chi_sim" "chi_tra" "jpn" "jpn_vert" ];
        };
      in {
	      # default 是对于 nix develop
        devShells.default = pkgs.mkShell {
          buildInputs = [
            python        # Python 解释器
            pkgs.uv       # uv 包管理器
            tesseractWithLangs
            pkgs."poppler-utils"
          ];
          shellHook = ''
            # 让 uv 使用 Nix 提供的 Python，而不是自己下载
            export UV_PYTHON="${python}/bin/python"
            # 禁止 uv 自动下载 Python（强制用 Nix 的）
            export UV_PYTHON_DOWNLOADS=never
            # 创建 venv 用于 pip 安装 nixpkgs 没有的包
            if [ ! -d .venv ]; then
              echo "创建 .venv..."
              uv venv --python "${python}/bin/python"
            fi
            # 自动同步依赖（根据 pyproject.toml + uv.lock）：保证 venv 和 uv.lock 一致
            if [ -f pyproject.toml ]; then
              uv sync --quiet
            fi
            source .venv/bin/activate
            echo "Python $(python --version) | 虚拟环境: $VIRTUAL_ENV"
          '';
        };
        formatter = pkgs.nixpkgs-fmt;
      }
    );
}
