"""Erros de domínio com mensagens prontas para o usuário final (pt-BR)."""


class ErroDominio(Exception):
    """Base de todos os erros de regra de negócio."""

    def __init__(self, mensagem: str, *, sugestao: str | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.sugestao = sugestao

    def __str__(self) -> str:
        if self.sugestao:
            return f"{self.mensagem} {self.sugestao}"
        return self.mensagem


class ValorInvalido(ErroDominio):
    """Valor monetário mal formatado ou fora dos limites."""


class ParcelasInvalidas(ErroDominio):
    """Quantidade de parcelas fora do intervalo permitido."""


class DataInvalida(ErroDominio):
    """Data mal formatada, inexistente ou futura quando proibido."""


class DescricaoInvalida(ErroDominio):
    """Descrição vazia ou longa demais."""
