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
      in {
	      # default 是对于 nix develop
        devShells.default = pkgs.mkShell {
          buildInputs = [
            python
          ];
          shellHook = ''
            # 创建 venv 用于 pip 安装 nixpkgs 没有的包
            if [ ! -d .venv ]; then
              echo "创建 .venv..."
              ${python}/bin/python -m venv .venv
            fi

            source .venv/bin/activate

            echo "Python $(python --version) | 虚拟环境: $VIRTUAL_ENV"
          '';
        };
        formatter = pkgs.nixpkgs-fmt;
      }
    );
}
